"""Hailo Whisper STT wrapper — encoder + decoder on Hailo-8 NPU.

Backends:
  - HailoWhisperBackend: Whisper-tiny/base on Hailo-8 (encoder + decoder HEFs),
    multilingual (Ukrainian via forced ``<|uk|>`` decoder prefix).
  - FasterWhisperBackend: faster-whisper int8 on CPU (fallback / dev machines).
  - MoonshineBackend (see moonshine_stt.py): English-only ONNX, optional.

Hailo Whisper path:
    audio bytes
        → float32 PCM (16 kHz, ≤ chunk_seconds)
        → mel spectrogram (host, numpy + scipy)
        → encoder HEF (Hailo NPU)
        → encoded features (numpy)
        → decoder HEF, autoregressive (Hailo NPU, ~seq_len iterations)
        → token argmax + repetition penalty (host)
        → tokenizer.decode → transcription text

CPU fallback path:
    audio bytes
        → FasterWhisperBackend (ctranslate2 int8)
        → transcription text

See hub.edge.voice.scheduler for NPU contention coordination with CV cascade.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import queue
import threading
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from hub.edge.voice.whisper_assets import WhisperAssets, ensure_assets
from hub.edge.voice.whisper_preprocess import audio_to_mel

logger = logging.getLogger(__name__)

try:
    from hailo_platform import HEF, FormatType, HailoSchedulingAlgorithm, VDevice

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

try:
    from faster_whisper import WhisperModel

    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    WhisperModel = None  # type: ignore[assignment,misc]
    FASTER_WHISPER_AVAILABLE = False

try:
    from transformers import AutoTokenizer  # type: ignore[import-not-found]

    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False

DEFAULT_LANGUAGE = "uk"
DEFAULT_VARIANT = "tiny"
DEFAULT_CACHE_DIR = Path(os.environ.get("WHISPER_ASSETS_DIR", "/app/.whisper_cache"))

# Whisper task / format tokens (stable across multilingual variants)
_TOK_SOT = "<|startoftranscript|>"
_TOK_TRANSCRIBE = "<|transcribe|>"
_TOK_NOTIMESTAMPS = "<|notimestamps|>"
_TOK_SOT_PREV = "<|startofprev|>"

# Domain-specific initial_prompt that biases the decoder toward smart-home
# Ukrainian vocabulary.  Keep short — every token here reduces available
# generation space (whisper-tiny seq_len = 448).
_INITIAL_PROMPT = (
    "Розумний дім. Увімкни лампу. Вимкни лампу. Лампа, реле, замок, термостат. "
    "Вітальня, кухня, спальня, коридор, ванна. Відкрий, закрий, перемкни. "
    "Встанови таймер. Яскравість, температура, гучність."
)

# Punctuation token ids exempt from repetition penalty (commas, periods)
_PUNCT_TOKENS = {11, 13}


class STTBackend(Protocol):
    async def transcribe(self, audio_bytes: bytes) -> str: ...


def _apply_repetition_penalty(
    logits: np.ndarray, generated: list[int], penalty: float = 1.5, window: int = 4
) -> np.ndarray:
    """Down-weight recently generated tokens to reduce loops. Mirrors the
    hailo-apps speech_recognition postprocessing.
    """
    logits = np.squeeze(logits, axis=0).astype(np.float32, copy=True)
    for tok in set(generated[-window:]):
        if tok not in _PUNCT_TOKENS:
            logits[tok] /= penalty
    return logits


class HailoWhisperBackend:
    """Whisper encoder + decoder on Hailo-8 NPU via HailoRT InferModel API.

    Adapted from the Apache-2 reference at
    hailo-ai/hailo-apps/hailo_apps/python/standalone_apps/speech_recognition/whisper_pipeline.py
    (run loop ported into a daemon thread driven by an asyncio queue).
    """

    def __init__(
        self,
        assets: WhisperAssets,
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        if not HAILO_AVAILABLE:
            raise RuntimeError("hailo_platform not installed")
        if not TRANSFORMERS_AVAILABLE:
            raise RuntimeError("transformers not installed (needed for tokenizer)")

        self._assets = assets
        self._language = language

        self._tokenizer = AutoTokenizer.from_pretrained(f"openai/whisper-{assets.variant}")
        sot_id = self._tokenizer.convert_tokens_to_ids(_TOK_SOT)
        lang_id = self._tokenizer.convert_tokens_to_ids(f"<|{language}|>")
        if lang_id is None or lang_id == self._tokenizer.unk_token_id:
            raise RuntimeError(
                f"Whisper tokenizer has no <|{language}|> token — pick a supported language code"
            )
        task_id = self._tokenizer.convert_tokens_to_ids(_TOK_TRANSCRIBE)
        nots_id = self._tokenizer.convert_tokens_to_ids(_TOK_NOTIMESTAMPS)

        # Prepend initial_prompt via <|startofprev|> + prompt tokens so the
        # decoder is biased toward smart-home Ukrainian vocabulary.
        # Layout: [SOT_PREV, *prompt_ids, SOT, lang, task, notimestamps]
        sot_prev_id = self._tokenizer.convert_tokens_to_ids(_TOK_SOT_PREV)
        prompt_ids: list[int] = []
        if sot_prev_id and sot_prev_id != self._tokenizer.unk_token_id:
            encoded = self._tokenizer.encode(_INITIAL_PROMPT, add_special_tokens=False)
            # Cap at 224 tokens (half of whisper-tiny seq_len) to leave room for generation
            prompt_ids = [int(sot_prev_id)] + [int(t) for t in encoded[:224]]

        self._forced_decoder_ids = prompt_ids + [
            int(sot_id),
            int(lang_id),
            int(task_id),
            int(nots_id),
        ]
        self._eos_id = int(self._tokenizer.eos_token_id)

        # Decoder embedding (operator stripped from HEF — runs on host)
        self._tok_embed = np.load(assets.token_embedding_npy)
        self._add_input = np.load(assets.onnx_add_input_npy)

        # Cross-thread plumbing: requests in, transcriptions/errors out
        self._req_q: queue.Queue[tuple[int, np.ndarray] | None] = queue.Queue()
        self._res_q: queue.Queue[tuple[int, str | BaseException]] = queue.Queue()
        self._counter = 0
        self._stop = threading.Event()

        # Cached at load() — populated by the worker thread once HEFs are configured
        self._chunk_samples = assets.chunk_seconds * 16000

        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="hailo-whisper")
        self._thread.start()
        logger.info(
            "Hailo Whisper backend ready — variant=%s lang=%s window=%ds",
            assets.variant,
            language,
            assets.chunk_seconds,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def transcribe(self, audio_bytes: bytes) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes)

    def close(self) -> None:
        self._stop.set()
        self._req_q.put(None)
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------ #
    # Sync entry — preprocess + RPC to worker thread
    # ------------------------------------------------------------------ #

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        from hub.edge.voice.audio_io import is_raw_pcm

        if is_raw_pcm(audio_bytes):
            audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            # Browser PTT (WebM/OGG/MP4) — decode via ffmpeg to 16 kHz mono.
            import subprocess
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
                f.write(audio_bytes)
                src = f.name
            try:
                out = subprocess.run(
                    [
                        "ffmpeg",
                        "-loglevel",
                        "error",
                        "-i",
                        src,
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-f",
                        "s16le",
                        "-",
                    ],
                    capture_output=True,
                    check=True,
                )
            finally:
                os.unlink(src)
            audio = np.frombuffer(out.stdout, dtype=np.int16).astype(np.float32) / 32768.0

        mel = audio_to_mel(audio, self._assets.chunk_seconds, str(self._assets.mel_filters_npz))

        # Issue request; block on the matching reply (queue is ordered per-thread)
        self._counter += 1
        req_id = self._counter
        self._req_q.put((req_id, mel))
        while True:
            reply_id, payload = self._res_q.get()
            if reply_id != req_id:
                # Out-of-order completion (shouldn't happen with FIFO worker, but be safe)
                self._res_q.put((reply_id, payload))
                continue
            if isinstance(payload, BaseException):
                raise payload
            return payload

    # ------------------------------------------------------------------ #
    # Worker thread — owns the Hailo VDevice for its entire lifetime
    # ------------------------------------------------------------------ #

    def _worker_loop(self) -> None:
        try:
            self._run_inference()
        except BaseException as exc:  # noqa: BLE001 — surface to all pending callers
            if "PHYSICAL_DEVICES" in str(exc):
                logger.error(
                    "Hailo Whisper worker died: NPU is exclusively held by another "
                    "process. Enable HailoRT multi-process sharing:\n"
                    "  sudo systemctl enable --now hailort.service\n"
                    "and confirm CV pipeline.py was restarted with group_id='SHARED' + "
                    "multi_process_service=True."
                )
            else:
                logger.exception("Hailo Whisper worker died")
            # Drain any pending requests with the failure
            while True:
                try:
                    item = self._req_q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    continue
                self._res_q.put((item[0], exc))

    def _run_inference(self) -> None:
        params = VDevice.create_params()
        # Cross-process Hailo-8 sharing with the CV systemd service. Requires
        # all three of:
        #   - hailort.service daemon running
        #   - multi_process_service = True (opt-in to the daemon from Python)
        #   - group_id = "SHARED" (matches what CV pipeline.py sets)
        # Any one missing → HAILO_OUT_OF_PHYSICAL_DEVICES on the second VDevice().
        params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
        params.group_id = "SHARED"
        if hasattr(params, "multi_process_service"):
            params.multi_process_service = True

        decoder_hef = HEF(str(self._assets.decoder_hef))
        decoder_model_name = decoder_hef.get_network_group_names()[0]
        sorted_outputs = decoder_hef.get_sorted_output_names()
        useful_outputs = [n for n in sorted_outputs if "conv" in n]
        seq_len = decoder_hef.get_output_vstream_infos()[0].shape[1]

        with VDevice(params) as vdevice:
            enc_model = vdevice.create_infer_model(str(self._assets.encoder_hef))
            dec_model = vdevice.create_infer_model(str(self._assets.decoder_hef))

            enc_model.input().set_format_type(FormatType.FLOAT32)
            enc_model.output().set_format_type(FormatType.FLOAT32)
            dec_model.input(f"{decoder_model_name}/input_layer1").set_format_type(
                FormatType.FLOAT32
            )
            dec_model.input(f"{decoder_model_name}/input_layer2").set_format_type(
                FormatType.FLOAT32
            )
            for name in sorted_outputs:
                dec_model.output(name).set_format_type(FormatType.FLOAT32)

            with enc_model.configure() as enc_cfg, dec_model.configure() as dec_cfg:
                enc_bindings = enc_cfg.create_bindings()
                dec_bindings = dec_cfg.create_bindings()
                timeout_ms = 30_000

                while not self._stop.is_set():
                    item = self._req_q.get()
                    if item is None:
                        return
                    req_id, mel = item
                    try:
                        text = self._infer_one(
                            mel,
                            enc_cfg,
                            enc_bindings,
                            enc_model,
                            dec_cfg,
                            dec_bindings,
                            dec_model,
                            decoder_model_name,
                            sorted_outputs,
                            useful_outputs,
                            seq_len,
                            timeout_ms,
                        )
                        self._res_q.put((req_id, text))
                    except BaseException as exc:  # noqa: BLE001
                        logger.exception("Hailo Whisper inference failed for req=%d", req_id)
                        self._res_q.put((req_id, exc))

    def _infer_one(
        self,
        mel: np.ndarray,
        enc_cfg: Any,
        enc_bindings: Any,
        enc_model: Any,
        dec_cfg: Any,
        dec_bindings: Any,
        dec_model: Any,
        decoder_model_name: str,
        sorted_outputs: list[str],
        useful_outputs: list[str],
        seq_len: int,
        timeout_ms: int,
    ) -> str:
        # ---- Encoder ---------------------------------------------------
        enc_bindings.input().set_buffer(np.ascontiguousarray(mel))
        enc_buf = np.zeros(enc_model.output().shape, dtype=np.float32)
        enc_bindings.output().set_buffer(enc_buf)
        enc_cfg.run([enc_bindings], timeout_ms)
        encoded = enc_bindings.output().get_buffer()

        # ---- Decoder (autoregressive) ----------------------------------
        dec_ids = np.zeros((1, seq_len), dtype=np.int64)
        for k, tok in enumerate(self._forced_decoder_ids):
            if k >= seq_len:
                break
            dec_ids[0][k] = tok
        free_start = len(self._forced_decoder_ids) - 1
        generated: list[int] = []

        for i in range(free_start, seq_len - 1):
            tok_embed = self._tokenize_host(dec_ids)

            dec_bindings.input(f"{decoder_model_name}/input_layer1").set_buffer(
                np.ascontiguousarray(encoded)
            )
            dec_bindings.input(f"{decoder_model_name}/input_layer2").set_buffer(
                np.ascontiguousarray(tok_embed)
            )
            for name in sorted_outputs:
                buf = np.zeros(dec_model.output(name).shape, dtype=np.float32)
                dec_bindings.output(name).set_buffer(buf)

            dec_cfg.run([dec_bindings], timeout_ms)

            outputs = np.concatenate(
                [dec_bindings.output(n).get_buffer() for n in useful_outputs],
                axis=2,
            )
            logits = _apply_repetition_penalty(outputs[:, i], generated)
            next_tok = int(np.argmax(logits))
            generated.append(next_tok)
            dec_ids[0][i + 1] = next_tok

            if next_tok == self._eos_id:
                break

        text: str = self._tokenizer.decode(generated, skip_special_tokens=True).strip()
        return text

    def _tokenize_host(self, dec_ids: np.ndarray) -> np.ndarray:
        """Gather + Add + Unsqueeze + Transpose — stripped from the Hailo-8
        decoder HEF during compile, must run on host.

        Output layout MUST be NHWC = (1, seq_len, 1, d_model). The DFC-SDK
        reference in hailocs/hailo-whisper does this in two transposes
        ((0,3,2,1) inside tokenize, then (0,2,3,1) at the call site); the
        production runtime in hailo-ai/hailo-apps fuses them into a single
        (0,2,1,3) transpose. We follow the runtime path — feeding the
        SDK-only first transpose alone produces garbage transcriptions
        (the decoder reads d_model as seq_len and vice-versa).
        """
        gather = self._tok_embed[dec_ids]  # (1, seq_len, d_model)
        added = gather + self._add_input
        nchw = np.expand_dims(added, axis=0)  # (1, 1, seq_len, d_model)
        return np.transpose(nchw, (0, 2, 1, 3))  # (1, seq_len, 1, d_model) — NHWC


class FasterWhisperBackend:
    """CPU STT using faster-whisper (int8).

    Default model: "base" (~145 MB, ~150-400 ms on RPi 5 ARM Cortex-A76).
    Override via FASTER_WHISPER_MODEL env var (e.g. "small", "tiny").
    """

    def __init__(
        self,
        model_size: str = "base",
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        if not FASTER_WHISPER_AVAILABLE:
            raise RuntimeError("faster-whisper not installed: pip install faster-whisper")
        self._language = language
        logger.info("Loading faster-whisper %s (int8) …", model_size)
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

    async def transcribe(self, audio_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._transcribe_sync, audio_bytes
        )

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        import subprocess
        import tempfile

        from hub.edge.voice.audio_io import is_raw_pcm

        logger.debug("transcribe: %d bytes, header=%s", len(audio_bytes), audio_bytes[:16].hex())

        if is_raw_pcm(audio_bytes):
            pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
                f.write(audio_bytes)
                src = f.name
            try:
                result = subprocess.run(
                    [
                        "ffmpeg",
                        "-loglevel",
                        "error",
                        "-i",
                        src,
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        "-f",
                        "s16le",
                        "-",
                    ],
                    capture_output=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                raise RuntimeError(
                    f"ffmpeg decode failed (stderr={e.stderr.decode()!r}, "
                    f"header={audio_bytes[:16].hex()!r})"
                ) from e
            finally:
                os.unlink(src)
            pcm = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0

        segments, _ = self._model.transcribe(
            pcm,
            language=self._language,
            initial_prompt=_INITIAL_PROMPT,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments)


def get_backend(
    force_cpu: bool = False,
    language: str = DEFAULT_LANGUAGE,
    npu_timeout_sec: float = 5.0,  # back-compat — unused
    moonshine_model: str | None = None,
    variant: str | None = None,
    assets_cache_dir: Path | None = None,
) -> STTBackend:
    """Return the best available STT backend.

    **Design decision of record:** STT runs on the CPU so the Hailo-8 NPU stays
    dedicated to the CV cascade (no NPU contention). Hailo Whisper is therefore
    *opt-in*, never the default.

    Selection is controlled by the ``STT_BACKEND`` env var (default ``auto``):

      ``auto`` / ``moonshine`` / ``faster_whisper``
          CPU backends. Priority: Moonshine ONNX (only when ``moonshine_model``
          is set *and* loads) → faster-whisper int8.
      ``hailo``
          Opt-in Hailo Whisper (encoder+decoder HEFs on the NPU); shares the NPU
          with CV via ``NPUScheduler``. Ignored when ``force_cpu=True``.

    Ukrainian note: the bundled ``moonshine_onnx`` models are English-only.
    ``moonshine-tiny-uk`` ships as safetensors (no ONNX); Ukrainian Moonshine
    ONNX exists only at *base* size (``moonshine-base-uk``) via the
    ``moonshine-voice`` package or sherpa-onnx. Until that is wired the working
    Ukrainian CPU engine is faster-whisper (``language="uk"``).
    """
    from hub.edge.voice.moonshine_stt import (
        MOONSHINE_AVAILABLE,
        MoonshineBackend,
        MoonshineUkBackend,
        moonshine_uk_available,
    )

    selector = os.environ.get("STT_BACKEND", "auto").strip().lower()
    target_variant = (variant or os.environ.get("WHISPER_VARIANT") or DEFAULT_VARIANT).lower()
    cache_dir = assets_cache_dir or DEFAULT_CACHE_DIR
    moonshine_onnx_dir = os.environ.get("MOONSHINE_ONNX_DIR")

    def _hailo() -> STTBackend | None:
        if force_cpu or not (HAILO_AVAILABLE and TRANSFORMERS_AVAILABLE):
            return None
        try:
            assets = ensure_assets(target_variant, cache_dir)
            logger.info("STT backend: Hailo Whisper (variant=%s, NPU)", target_variant)
            return HailoWhisperBackend(assets, language=language)
        except Exception as exc:
            logger.warning("Hailo Whisper init failed (%s) — falling through", exc)
            return None

    def _moonshine_uk() -> STTBackend | None:
        # Working Ukrainian CPU engine: locally-exported Moonshine-base-uk ONNX
        # (no torch/optimum at runtime). Preferred default when present.
        if not moonshine_uk_available(moonshine_onnx_dir):
            return None
        try:
            logger.info("STT backend: Moonshine-uk ONNX (%s, CPU)", moonshine_onnx_dir)
            return MoonshineUkBackend(moonshine_onnx_dir)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning("Moonshine-uk backend failed (%s) — falling through", exc)
            return None

    def _moonshine() -> STTBackend | None:
        if not (MOONSHINE_AVAILABLE and moonshine_model):
            return None
        try:
            logger.info("STT backend: Moonshine ONNX (%s, CPU)", moonshine_model)
            return MoonshineBackend(model_name=moonshine_model)
        except Exception as exc:
            logger.warning("Moonshine backend failed (%s) — falling through", exc)
            return None

    def _faster() -> STTBackend | None:
        if not FASTER_WHISPER_AVAILABLE:
            return None
        model_size = os.environ.get("FASTER_WHISPER_MODEL", "base")
        logger.info("STT backend: faster-whisper %s (int8, CPU, language=%s)", model_size, language)
        return FasterWhisperBackend(model_size=model_size, language=language)

    if selector == "hailo":
        # Opt-in NPU path (falls back to CPU if Hailo is unavailable / force_cpu).
        backend = _hailo() or _moonshine_uk() or _moonshine() or _faster()
    elif selector in ("moonshine-uk", "moonshine_uk"):
        # Explicitly pin the Ukrainian Moonshine ONNX engine.
        backend = _moonshine_uk() or _faster()
    else:
        # Default: CPU-first. Moonshine-uk ONNX is the working Ukrainian engine
        # (faster + more accurate than faster-whisper-base on RPi 5); fall back to
        # faster-whisper, then English Moonshine, then the NPU as a last resort.
        backend = _moonshine_uk() or _faster() or _moonshine() or _hailo()

    if backend is None:
        raise RuntimeError(
            "No STT backend available — export Moonshine-uk ONNX "
            "(training.export_moonshine_onnx) or install faster-whisper (CPU) / "
            "hailo_platform+transformers (NPU)"
        )
    return backend


async def transcribe_file(
    audio_path: Path,
    force_cpu: bool = False,
    language: str = DEFAULT_LANGUAGE,
    variant: str | None = None,
) -> str:
    backend = get_backend(force_cpu=force_cpu, language=language, variant=variant)
    return await backend.transcribe(audio_path.read_bytes())


if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Transcribe a WAV file")
    parser.add_argument("--record", type=pathlib.Path, required=True)
    parser.add_argument("--force-cpu", action="store_true")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--variant", default=None, help="tiny | base (Hailo path only)")
    parser.add_argument("--bench", action="store_true")
    args = parser.parse_args()

    if args.bench:
        times = []
        for _ in range(3):
            t0 = time.monotonic()
            result = asyncio.run(
                transcribe_file(
                    args.record,
                    force_cpu=args.force_cpu,
                    language=args.language,
                    variant=args.variant,
                )
            )
            times.append((time.monotonic() - t0) * 1000)
        print(f"Result: {result}")
        print(
            f"Latency: {min(times):.0f}/{sum(times) / len(times):.0f}/{max(times):.0f} ms (min/avg/max)"
        )
    else:
        result = asyncio.run(
            transcribe_file(
                args.record,
                force_cpu=args.force_cpu,
                language=args.language,
                variant=args.variant,
            )
        )
        print(result)

"""Hailo Whisper STT wrapper — encoder on Hailo NPU, decoder on CPU.

Primary STT is Moonshine ONNX (see moonshine_stt.py).  This module provides:
  - HailoWhisperBackend: Whisper encoder on Hailo-8 NPU + CPU decoder (secondary)
  - FasterWhisperBackend: faster-whisper large-v3-turbo CPU (tertiary fallback)
  - get_backend(): selects Moonshine → Hailo → faster-whisper in priority order

Hybrid architecture (Hailo path, ~250ms target):
    audio bytes
        → float32 numpy array (16 kHz)
        → HailoWhisperBackend.encode()   # mel-spectrogram + encoder on Hailo NPU
        → encoder features (numpy)
        → HailoWhisperBackend.decode()   # autoregressive decoder on CPU
        → transcription text

CPU fallback path (~500ms on RPi 5, ARM Cortex-A76):
    audio bytes
        → FasterWhisperBackend           # large-v3-turbo int8
        → transcription text

See hub.edge.voice.scheduler for NPU contention coordination with CV cascade.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

try:
    from hailo_platform import HEF, VDevice  # noqa: F401

    HAILO_AVAILABLE = True
except ImportError:
    HAILO_AVAILABLE = False

try:
    from faster_whisper import WhisperModel

    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False

# Target language; override via HailoWhisperBackend(language=...) for multilingual
DEFAULT_LANGUAGE = "uk"

# How long to wait for NPU encoder before falling back to CPU
DEFAULT_NPU_TIMEOUT_SEC = 5.0


class STTBackend(Protocol):
    async def transcribe(self, audio_bytes: bytes) -> str: ...


class HailoWhisperBackend:
    """Whisper encoder on Hailo-8 NPU, autoregressive decoder on CPU.

    Requires hailo_platform + official Hailo Whisper HEF from Hailo Model Zoo
    (announced July 2025 — check hailo-ai/hailo_model_zoo for availability).

    Falls back to FasterWhisperBackend automatically on timeout or NPU error,
    ensuring the voice pipeline never stalls even under CV cascade load.
    """

    def __init__(
        self,
        hef_path: Path,
        language: str = DEFAULT_LANGUAGE,
        npu_timeout_sec: float = DEFAULT_NPU_TIMEOUT_SEC,
    ) -> None:
        if not HAILO_AVAILABLE:
            raise RuntimeError("hailo_platform not installed — run on RPi5 with HailoRT")
        self._hef_path = hef_path
        self._language = language
        self._npu_timeout_sec = npu_timeout_sec
        self._device: Any = None
        self._network_group: Any = None
        # CPU fallback ready at construction time so it's warm for fast escalation
        self._cpu_fallback: FasterWhisperBackend | None = (
            FasterWhisperBackend(language=language) if FASTER_WHISPER_AVAILABLE else None
        )

    def load(self) -> None:
        """Load HEF into Hailo device. Call once before transcribe()."""
        hef = HEF(str(self._hef_path))
        self._device = VDevice()
        configure_params = self._device.create_configure_params(hef)
        network_groups = self._device.configure(hef, configure_params)
        self._network_group = network_groups[0]
        logger.info("Hailo Whisper loaded: %s", self._hef_path.name)

    def encode(self, audio_f32: Any) -> Any:
        """Run mel-spectrogram + Whisper encoder on Hailo NPU.

        audio_f32: float32 numpy array, shape (N,), 16 kHz mono, range [-1, 1]
        Returns encoder features (numpy array, shape matches Whisper enc output).

        Note: actual Hailo stream API call not yet wired — see
        https://github.com/hailo-ai/hailo-whisper for reference implementation.
        """
        if self._network_group is None:
            raise RuntimeError("Call load() before encode()")
        raise NotImplementedError("Hailo Whisper encoder not wired — see hailo-ai/hailo-whisper")

    def decode(self, features: Any, language: str | None = None) -> str:
        """Run Whisper decoder on CPU given encoder features.

        features: numpy array from encode()
        Returns transcription string.
        """
        raise NotImplementedError(
            "Hailo Whisper decoder not wired — needs transformers or whisper-cpp"
        )

    async def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe audio. Uses Hailo NPU with timeout, falls back to CPU."""
        try:
            return await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, self._transcribe_hailo, audio_bytes),
                timeout=self._npu_timeout_sec,
            )
        except (TimeoutError, NotImplementedError, RuntimeError) as exc:
            logger.warning(
                "Hailo Whisper unavailable (%s) — falling back to CPU", type(exc).__name__
            )
            if self._cpu_fallback is not None:
                return await self._cpu_fallback.transcribe(audio_bytes)
            raise RuntimeError("No STT fallback available") from exc

    def _transcribe_hailo(self, audio_bytes: bytes) -> str:
        import io

        try:
            # import numpy as np
            import soundfile
        except ImportError as e:
            raise RuntimeError("Install numpy + soundfile for Hailo Whisper") from e

        audio_f32, sr = soundfile.read(io.BytesIO(audio_bytes))
        if sr != 16000:
            raise RuntimeError(f"Expected 16 kHz audio, got {sr} Hz")
        features = self.encode(audio_f32.astype("float32"))
        return self.decode(features, language=self._language)

    def close(self) -> None:
        if self._device is not None:
            self._device.release()
            self._device = None


class FasterWhisperBackend:
    """CPU STT using faster-whisper (int8).

    Default model: "small" (~244 MB, ~300 ms on RPi 5 ARM Cortex-A76, WER ~8% uk).
    Override via FASTER_WHISPER_MODEL env var (e.g. "medium", "large-v3-turbo").
    """

    def __init__(
        self,
        model_size: str = "small",
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
        import os
        import subprocess
        import tempfile

        import numpy as np

        logger.debug("transcribe: %d bytes, header=%s", len(audio_bytes), audio_bytes[:16].hex())

        # Use system ffmpeg to decode any container (WebM/OGG/WAV/MP4) to raw
        # 16 kHz int16 mono PCM, then hand a float32 numpy array to the model.
        # This bypasses faster-whisper's internal PyAV call, which fails on some
        # WebM streams from browser MediaRecorder.
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
            initial_prompt=(
                "Розумний дім. Увімкни, вимкни, відкрий, закрий, перемкни. "
                "Встанови таймер. Збільш, зменш гучність. Що сталось. Звіт."
            ),
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments)


def get_backend(
    hef_path: Path | None = None,
    force_cpu: bool = False,
    language: str = DEFAULT_LANGUAGE,
    npu_timeout_sec: float = DEFAULT_NPU_TIMEOUT_SEC,
    moonshine_model: str | None = None,
) -> STTBackend:
    """Return best available STT backend.

    Priority: Moonshine ONNX (explicit model only) → Hailo NPU → faster-whisper CPU.

    Moonshine is only attempted when moonshine_model is explicitly set.
    Note: UsefulSensors/moonshine-tiny-uk has no ONNX export — use faster-whisper
    with language="uk" for Ukrainian STT (set MOONSHINE_MODEL="" to skip moonshine).
    """
    import os

    from hub.edge.voice.moonshine_stt import MOONSHINE_AVAILABLE, MoonshineBackend

    if MOONSHINE_AVAILABLE and moonshine_model:
        try:
            logger.info("Moonshine ONNX backend: %s", moonshine_model)
            return MoonshineBackend(model_name=moonshine_model)
        except Exception as exc:
            logger.warning("Moonshine backend failed (%s) — falling through", exc)

    if not force_cpu and HAILO_AVAILABLE and hef_path is not None and hef_path.exists():
        logger.info("Hailo Whisper backend: %s", hef_path.name)
        backend = HailoWhisperBackend(hef_path, language=language, npu_timeout_sec=npu_timeout_sec)
        backend.load()
        return backend

    if FASTER_WHISPER_AVAILABLE:
        model_size = os.environ.get("FASTER_WHISPER_MODEL", "small")
        logger.info("faster-whisper %s (int8, language=%s)", model_size, language)
        return FasterWhisperBackend(model_size=model_size, language=language)

    raise RuntimeError("No STT backend available — install useful-moonshine-onnx or faster-whisper")


async def transcribe_file(
    audio_path: Path,
    hef_path: Path | None = None,
    force_cpu: bool = False,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    backend = get_backend(hef_path=hef_path, force_cpu=force_cpu, language=language)
    return await backend.transcribe(audio_path.read_bytes())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Transcribe a WAV file")
    parser.add_argument("--record", type=pathlib.Path, required=True)
    parser.add_argument("--hef", type=pathlib.Path, default=None)
    parser.add_argument("--force-cpu", action="store_true")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE)
    parser.add_argument("--bench", action="store_true", help="Print latency stats")
    args = parser.parse_args()

    import time

    if args.bench:
        times = []
        for _ in range(3):
            t0 = time.monotonic()
            result = asyncio.run(
                transcribe_file(args.record, hef_path=args.hef, force_cpu=args.force_cpu)
            )
            times.append((time.monotonic() - t0) * 1000)
        print(f"Result: {result}")
        print(
            f"Latency: {min(times):.0f}/{sum(times)/len(times):.0f}/{max(times):.0f} ms (min/avg/max)"
        )
    else:
        result = asyncio.run(
            transcribe_file(
                args.record, hef_path=args.hef, force_cpu=args.force_cpu, language=args.language
            )
        )
        print(result)

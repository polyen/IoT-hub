"""Moonshine ONNX STT backend.

Supported models (the bundled ``moonshine_onnx`` package is English-only):
  "moonshine/tiny"  — 27 M params, ~26 MB ONNX, ~100–150 ms on RPi 5
  "moonshine/base"  — 61 M params, ~61 MB ONNX, ~250 ms on RPi 5

Ukrainian note: UsefulSensors/moonshine-tiny-uk ships only as SafeTensors (no
ONNX export), so it cannot be loaded here. Ukrainian Moonshine is available as
ONNX only at *base* size (moonshine-base-uk) via the ``moonshine-voice`` package
or sherpa-onnx. Until that backend is wired, use faster-whisper with
language="uk" (leave MOONSHINE_MODEL empty) for Ukrainian STT.

Package: pip install useful-moonshine-onnx
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    import soundfile
    from moonshine_onnx import MoonshineOnnxModel

    MOONSHINE_AVAILABLE = True
except ImportError:
    soundfile = None
    MOONSHINE_AVAILABLE = False

try:
    import tokenizers  # type: ignore[import-untyped]

    TOKENIZERS_AVAILABLE = True
except ImportError:
    TOKENIZERS_AVAILABLE = False

DEFAULT_MOONSHINE_MODEL = "moonshine/tiny"

# Moonshine emits ~6.5 tokens per second of audio; cap generation a little above
# that (rather than the model's flat max_length) so short commands neither
# truncate nor run away into repetition.
_TOKENS_PER_SEC = 6.5


class MoonshineBackend:
    """STT backend using Moonshine ONNX.

    The bundled ``moonshine_onnx`` package ships English models only
    ("moonshine/tiny", "moonshine/base"). Ukrainian requires moonshine-voice /
    sherpa-onnx ``moonshine-base-uk`` (see module docstring).
    """

    def __init__(self, model_name: str = DEFAULT_MOONSHINE_MODEL) -> None:
        if not MOONSHINE_AVAILABLE:
            raise RuntimeError("moonshine-onnx not installed: pip install useful-moonshine-onnx")
        self._model_name = model_name
        self._model: Any = MoonshineOnnxModel(model_name=model_name)
        logger.info("Moonshine loaded: %s", model_name)

    async def transcribe(self, audio_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._transcribe_sync, audio_bytes
        )

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        import os
        import subprocess
        import tempfile

        from hub.edge.voice.audio_io import SAMPLE_RATE, is_raw_pcm

        # Mic / RTSP paths deliver headerless int16 PCM at SAMPLE_RATE — bypass
        # ffmpeg, which can't autodetect a raw stream. Only container blobs
        # (browser PTT WebM/OGG) need the transcode pass below.
        if is_raw_pcm(audio_bytes):
            audio_f32 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as src:
                src.write(audio_bytes)
                src_path = src.name
            wav_path = src_path + ".wav"
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-loglevel",
                        "error",
                        "-i",
                        src_path,
                        "-ar",
                        str(SAMPLE_RATE),
                        "-ac",
                        "1",
                        "-f",
                        "wav",
                        wav_path,
                    ],
                    check=True,
                )
                audio_f32, _sr = soundfile.read(wav_path, dtype="float32")
            finally:
                os.unlink(src_path)
                if os.path.exists(wav_path):
                    os.unlink(wav_path)
            if audio_f32.ndim > 1:
                audio_f32 = audio_f32.mean(axis=1)

        tokens = self._model.generate(audio_f32[np.newaxis, :])
        return str(self._model.tokenizer.decode_batch(tokens)[0]).strip()


def moonshine_uk_available(onnx_dir: Path | str | None) -> bool:
    """True when the Ukrainian Moonshine ONNX backend can actually load."""
    if not (MOONSHINE_AVAILABLE and TOKENIZERS_AVAILABLE and onnx_dir):
        return False
    d = Path(onnx_dir)
    return (
        (d / "encoder_model.onnx").exists()
        and (d / "decoder_model_merged.onnx").exists()
        and (d / "tokenizer.json").exists()
    )


class MoonshineUkBackend:
    """Ukrainian STT via locally-exported Moonshine-base-uk ONNX (CPU).

    Loads the ONNX produced by ``training/export_moonshine_onnx.py`` through the
    ``moonshine_onnx`` decode loop (plain ``onnxruntime`` — **no torch / no
    optimum** at runtime) and the Ukrainian ``tokenizer.json`` shipped alongside.

    This is the working Ukrainian CPU engine: measured on RPi 5 it transcribes a
    command in ~1.1 s (vs ~6 s for faster-whisper-base) and is functionally more
    accurate, so ``get_backend`` prefers it over faster-whisper when the ONNX
    directory is present.
    """

    def __init__(self, onnx_dir: Path | str) -> None:
        if not MOONSHINE_AVAILABLE:
            raise RuntimeError("moonshine-onnx not installed: pip install useful-moonshine-onnx")
        if not TOKENIZERS_AVAILABLE:
            raise RuntimeError("tokenizers not installed")
        self._dir = Path(onnx_dir)
        # model_name="base" only selects the decoder layer geometry; the actual
        # weights come from ``models_dir``.
        self._model: Any = MoonshineOnnxModel(
            models_dir=str(self._dir), model_name="base", model_format="onnx"
        )
        self._tokenizer = tokenizers.Tokenizer.from_file(str(self._dir / "tokenizer.json"))
        logger.info("Moonshine-uk ONNX loaded from %s", self._dir)

    async def transcribe(self, audio_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._transcribe_sync, audio_bytes
        )

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        import os
        import subprocess
        import tempfile

        from hub.edge.voice.audio_io import SAMPLE_RATE, is_raw_pcm

        if is_raw_pcm(audio_bytes):
            audio_f32 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        else:
            with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as src:
                src.write(audio_bytes)
                src_path = src.name
            wav_path = src_path + ".wav"
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-loglevel",
                        "error",
                        "-i",
                        src_path,
                        "-ar",
                        str(SAMPLE_RATE),
                        "-ac",
                        "1",
                        "-f",
                        "wav",
                        wav_path,
                    ],
                    check=True,
                )
                audio_f32, _sr = soundfile.read(wav_path, dtype="float32")
            finally:
                os.unlink(src_path)
                if os.path.exists(wav_path):
                    os.unlink(wav_path)
            if audio_f32.ndim > 1:
                audio_f32 = audio_f32.mean(axis=1)

        max_len = int(len(audio_f32) / SAMPLE_RATE * _TOKENS_PER_SEC) + 8
        tokens = self._model.generate(audio_f32[np.newaxis, :].astype(np.float32), max_len=max_len)
        return str(self._tokenizer.decode_batch(tokens)[0]).strip()

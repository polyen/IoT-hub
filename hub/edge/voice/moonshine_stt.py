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
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    import soundfile
    from moonshine_onnx import MoonshineOnnxModel

    MOONSHINE_AVAILABLE = True
except ImportError:
    soundfile = None  # type: ignore[assignment]
    MOONSHINE_AVAILABLE = False

DEFAULT_MOONSHINE_MODEL = "moonshine/tiny"


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
        self._model: Any = MoonshineOnnxModel(model_name=model_name)  # type: ignore[name-defined]
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
        return self._model.tokenizer.decode_batch(tokens)[0].strip()

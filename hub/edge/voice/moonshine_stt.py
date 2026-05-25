"""Moonshine ONNX STT backend.

Supported models (all English):
  "moonshine/tiny"  — 27 M params, ~26 MB ONNX, ~100–150 ms on RPi 5
  "moonshine/base"  — 61 M params, ~61 MB ONNX, ~250 ms on RPi 5

Note: UsefulSensors/moonshine-tiny-uk exists only as SafeTensors (no ONNX export).
For Ukrainian STT use faster-whisper with language="uk" (set MOONSHINE_MODEL="").

Package: pip install useful-moonshine-onnx
"""

from __future__ import annotations

import asyncio
import io
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
    """Primary STT backend using Moonshine ONNX (Ukrainian-specific tiny model)."""

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
        audio_f32, sr = soundfile.read(io.BytesIO(audio_bytes), dtype="float32")
        if audio_f32.ndim > 1:
            audio_f32 = audio_f32.mean(axis=1)
        if sr != 16000:
            raise RuntimeError(f"Expected 16 kHz audio, got {sr} Hz")

        tokens = self._model.generate(audio_f32[np.newaxis, :])
        return self._model.tokenizer.decode_batch(tokens)[0].strip()

"""Hailo Whisper STT wrapper — encoder on Hailo NPU, decoder on CPU.

Falls back to faster-whisper distil-large-v3 if Hailo pipeline unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
from pathlib import Path
from typing import Protocol

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


class STTBackend(Protocol):
    async def transcribe(self, audio_bytes: bytes) -> str: ...


class HailoWhisperBackend:
    """Whisper encoder on Hailo NPU, decoder on CPU."""

    def __init__(self, hef_path: Path) -> None:
        if not HAILO_AVAILABLE:
            raise RuntimeError("hailo_platform not installed — run on RPi5 with HailoRT")
        self._hef_path = hef_path

    async def transcribe(self, audio_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._transcribe_sync, audio_bytes
        )

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        # TODO: implement Hailo Whisper encoder pass + CPU decoder
        # See https://github.com/hailo-ai/hailo-whisper for pipeline details
        raise NotImplementedError("Hailo Whisper pipeline not yet wired — see hailo-whisper repo")


class FasterWhisperBackend:
    """CPU fallback using faster-whisper distil-large-v3."""

    def __init__(self, model_size: str = "distil-large-v3") -> None:
        if not FASTER_WHISPER_AVAILABLE:
            raise RuntimeError("faster-whisper not installed: pip install faster-whisper")
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

    async def transcribe(self, audio_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._transcribe_sync, audio_bytes
        )

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        import io

        import soundfile

        audio_array, _sr = soundfile.read(io.BytesIO(audio_bytes))
        segments, _ = self._model.transcribe(audio_array, language="uk")
        return " ".join(seg.text.strip() for seg in segments)


def get_backend(hef_path: Path | None = None, force_cpu: bool = False) -> STTBackend:
    """Return best available STT backend."""
    if not force_cpu and HAILO_AVAILABLE and hef_path and hef_path.exists():
        logger.info("Using Hailo Whisper backend")
        return HailoWhisperBackend(hef_path)
    if FASTER_WHISPER_AVAILABLE:
        logger.info("Using faster-whisper fallback (CPU)")
        return FasterWhisperBackend()
    raise RuntimeError("No STT backend available — install hailo_platform or faster-whisper")


async def transcribe_file(audio_path: Path, force_cpu: bool = False) -> str:
    backend = get_backend(force_cpu=force_cpu)
    return await backend.transcribe(audio_path.read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=pathlib.Path, required=True)
    parser.add_argument("--force-cpu", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(transcribe_file(args.record, force_cpu=args.force_cpu))
    print(result)

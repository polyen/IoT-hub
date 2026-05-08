"""Silero-VAD wrapper — async generator over microphone stream.

Yields audio chunks (bytes, 16kHz, 16-bit mono) that contain speech.
Filters silence to avoid unnecessary Whisper calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

try:
    import torch  # type: ignore[import]  # noqa: F401

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

SAMPLE_RATE = 16000
CHUNK_MS = 32
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000
SPEECH_THRESHOLD = 0.5


class SileroVAD:
    def __init__(self) -> None:
        if not TORCH_AVAILABLE:
            raise ImportError("torch not installed — required for silero-VAD")
        self._model: Any = None

    def load(self) -> None:
        """Download and cache silero-VAD model (first run only)."""
        import torch

        model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            onnx=False,
        )
        self._model = model

    def is_speech(self, audio_chunk: bytes) -> bool:
        """Returns True if chunk likely contains speech."""
        if self._model is None:
            raise RuntimeError("Call load() first")
        import torch

        audio = torch.frombuffer(audio_chunk, dtype=torch.int16).float() / 32768.0
        prob: float = self._model(audio, SAMPLE_RATE).item()
        return prob > SPEECH_THRESHOLD

    async def stream(self, device_index: int | None = None) -> AsyncGenerator[bytes, None]:
        """Yield speech chunks from microphone. Requires sounddevice."""
        try:
            import sounddevice as sd  # type: ignore[import]
        except ImportError as e:
            raise ImportError("sounddevice not installed") from e

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SAMPLES,
            dtype="int16",
            channels=1,
            device=device_index,
            callback=callback,
        ):
            while True:
                chunk = await queue.get()
                if self.is_speech(chunk):
                    yield chunk

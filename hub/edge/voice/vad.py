"""Silero-VAD wrapper — async generator over microphone stream.

Yields audio chunks (bytes, 16kHz, 16-bit mono) that contain speech.
Filters silence to avoid unnecessary Whisper calls.

Uses the silero-vad ONNX model directly via onnxruntime to avoid the
torchaudio dependency that the pip and hub versions of silero-vad pull in.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_MS = 32
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000
SPEECH_THRESHOLD = 0.5

# SILERO_VAD_ONNX_PATH is set to the pre-baked path in the Docker image.
# Falls back to the user cache dir for local dev (extracted from pip wheel on
# first run — same model format as the Docker image, no network download of the
# full wheel needed beyond pip's own cache).
_SILERO_VAD_VERSION = "6.2.1"
_CACHE_PATH = Path(
    os.environ.get(
        "SILERO_VAD_ONNX_PATH",
        str(Path.home() / ".cache" / "silero_vad" / "silero_vad.onnx"),
    )
)


def _ensure_model() -> Path:
    """Return the silero-vad ONNX path, extracting from the pip wheel if absent."""
    if _CACHE_PATH.exists():
        return _CACHE_PATH

    import subprocess
    import sys
    import tempfile
    import zipfile

    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Fetching silero-vad ONNX from pip wheel (one-time) …")
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--no-deps",
                f"silero-vad=={_SILERO_VAD_VERSION}",
                "-d",
                tmpdir,
            ],
            check=True,
            capture_output=True,
        )
        whl = next(Path(tmpdir).glob("*.whl"))
        _CACHE_PATH.write_bytes(zipfile.ZipFile(whl).read("silero_vad/data/silero_vad.onnx"))
    logger.info("Saved to %s", _CACHE_PATH)
    return _CACHE_PATH


class SileroVAD:
    def __init__(self) -> None:
        self._session: Any = None
        self._h: Any = None  # LSTM hidden state [2, 1, 64]
        self._c: Any = None  # LSTM cell state  [2, 1, 64]

    def load(self) -> None:
        """Download (once) and load silero-VAD ONNX model."""
        import numpy as np
        import onnxruntime as ort  # type: ignore[import]

        path = _ensure_model()
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(str(path), sess_options=opts)
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def _reset_states(self) -> None:
        import numpy as np

        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def is_speech(self, audio_chunk: bytes) -> bool:
        """Returns True if chunk likely contains speech."""
        if self._session is None:
            raise RuntimeError("Call load() first")
        import numpy as np

        audio = np.frombuffer(audio_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        audio = audio.reshape(1, -1)  # [1, chunk_samples]

        ort_inputs = {
            "input": audio,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
            "h": self._h,
            "c": self._c,
        }
        out, self._h, self._c = self._session.run(None, ort_inputs)
        prob: float = float(out.item())
        return prob > SPEECH_THRESHOLD

    async def filter_stream(
        self, source: AsyncGenerator[bytes, None]
    ) -> AsyncGenerator[bytes, None]:
        """Filter an external audio generator, yielding only speech chunks."""
        async for chunk in source:
            if self.is_speech(chunk):
                yield chunk

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

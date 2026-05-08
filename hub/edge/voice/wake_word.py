"""openWakeWord wrapper — detects wake word in audio stream.

Placeholder model: "hey_jarvis". Replace with custom "хей хата" after T2.6.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

try:
    import openwakeword  # type: ignore[import]  # noqa: F401
    from openwakeword.model import Model as OWWModel  # type: ignore[import]

    OWW_AVAILABLE = True
except ImportError:
    OWW_AVAILABLE = False

WAKE_WORD_THRESHOLD = 0.5
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280


class WakeWordDetector:
    def __init__(self, model_path: str | None = None) -> None:
        """
        model_path: path to custom .tflite/.onnx model (T2.6 output).
        None = download default "hey_jarvis" placeholder.
        """
        if not OWW_AVAILABLE:
            raise ImportError("openwakeword not installed")
        self._model_path = model_path
        self._model: OWWModel | None = None

    def load(self) -> None:
        if self._model_path:
            self._model = OWWModel(wakeword_models=[self._model_path], inference_framework="onnx")
        else:
            self._model = OWWModel(inference_framework="onnx")

    def detect(self, audio_chunk: bytes) -> bool:
        """Returns True if wake word detected in chunk."""
        if self._model is None:
            raise RuntimeError("Call load() first")
        import numpy as np  # type: ignore[import]

        audio = np.frombuffer(audio_chunk, dtype=np.int16)
        prediction = self._model.predict(audio)
        scores = list(prediction.values())
        return any(s > WAKE_WORD_THRESHOLD for s in scores)

    async def listen(self, audio_stream: AsyncGenerator[bytes, None]) -> AsyncGenerator[None, None]:
        """Yield once each time wake word is detected in stream."""
        async for chunk in audio_stream:
            if self.detect(chunk):
                yield

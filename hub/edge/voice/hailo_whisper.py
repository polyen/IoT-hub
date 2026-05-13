"""Hailo Whisper STT wrapper — encoder on Hailo NPU, decoder on CPU.

Falls back to faster-whisper large-v3-turbo (int8) if Hailo pipeline is
unavailable or NPU times out (contention with CV cascade).

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
    """CPU fallback using faster-whisper large-v3-turbo (int8, 5.4× faster than large-v3).

    large-v3-turbo delivers similar accuracy to large-v3 at distil-like speed,
    making it the preferred CPU fallback over the previous distil-large-v3.
    Benchmark result on RPi 5: compare via hub.edge.voice.stt --bench.
    """

    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        language: str = DEFAULT_LANGUAGE,
    ) -> None:
        if not FASTER_WHISPER_AVAILABLE:
            raise RuntimeError("faster-whisper not installed: pip install faster-whisper")
        self._language = language
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")

    async def transcribe(self, audio_bytes: bytes) -> str:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._transcribe_sync, audio_bytes
        )

    def _transcribe_sync(self, audio_bytes: bytes) -> str:
        import io

        import soundfile

        audio_array, _sr = soundfile.read(io.BytesIO(audio_bytes))
        segments, _ = self._model.transcribe(audio_array, language=self._language)
        return " ".join(seg.text.strip() for seg in segments)


def get_backend(
    hef_path: Path | None = None,
    force_cpu: bool = False,
    language: str = DEFAULT_LANGUAGE,
    npu_timeout_sec: float = DEFAULT_NPU_TIMEOUT_SEC,
) -> STTBackend:
    """Return best available STT backend.

    Priority: Hailo NPU (HEF file present + HailoRT available) → CPU fallback.
    On NPU contention or timeout, HailoWhisperBackend auto-escalates to CPU.
    """
    if not force_cpu and HAILO_AVAILABLE and hef_path is not None and hef_path.exists():
        logger.info("Using Hailo Whisper backend: %s", hef_path.name)
        backend = HailoWhisperBackend(hef_path, language=language, npu_timeout_sec=npu_timeout_sec)
        backend.load()
        return backend
    if FASTER_WHISPER_AVAILABLE:
        logger.info("Using faster-whisper fallback (large-v3-turbo, CPU)")
        return FasterWhisperBackend(language=language)
    raise RuntimeError("No STT backend available — install hailo_platform or faster-whisper")


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

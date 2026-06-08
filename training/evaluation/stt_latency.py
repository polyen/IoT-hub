"""STT latency benchmark: Hailo Whisper vs faster-whisper CPU."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import struct
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import numpy  # noqa: F401

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import soundfile  # noqa: F401

    SOUNDFILE_AVAILABLE = True
except ImportError:
    SOUNDFILE_AVAILABLE = False

try:
    from faster_whisper import WhisperModel

    FASTER_WHISPER_AVAILABLE = True
except ImportError:
    FASTER_WHISPER_AVAILABLE = False

SAMPLE_RATE = 16000
DURATION_S = 5


def _generate_sine_wav(duration_s: int = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Generate a sine wave WAV file in memory (no external deps)."""
    n_samples = duration_s * sample_rate
    freq = 440.0  # Hz

    raw_samples: bytes
    if NUMPY_AVAILABLE:
        import numpy as _np

        t = _np.linspace(0, duration_s, n_samples, dtype=_np.float32)
        wave = (_np.sin(2 * _np.pi * freq * t) * 32767).astype(_np.int16)
        raw_samples = bytes(wave.tobytes())
    else:
        import math

        samples_list: list[int] = []
        for i in range(n_samples):
            val = int(32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            samples_list.append(val)
        raw_samples = struct.pack(f"<{n_samples}h", *samples_list)

    # Build minimal WAV header
    data_size = len(raw_samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        1,  # mono
        sample_rate,
        sample_rate * 2,  # byte rate
        2,  # block align
        16,  # bits per sample
        b"data",
        data_size,
    )
    return header + raw_samples


class STTBenchmark:
    """Benchmark for comparing STT backend latencies."""

    def benchmark_faster_whisper(
        self,
        audio_path: str,
        model_size: str = "small",
        n_runs: int = 10,
    ) -> dict[str, Any]:
        """Time faster-whisper transcription over n_runs."""
        if not FASTER_WHISPER_AVAILABLE:
            return {
                "measured": False,
                "latency_mean_s": None,
                "latency_p95_s": None,
                "model": model_size,
                "note": "faster-whisper not installed",
            }

        try:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            audio_file = Path(audio_path)
            latencies: list[float] = []

            for _ in range(n_runs):
                t0 = time.perf_counter()
                if SOUNDFILE_AVAILABLE:
                    import soundfile as sf

                    audio_array, _sr = sf.read(str(audio_file))
                    segments, _ = model.transcribe(audio_array, language="uk")
                else:
                    segments, _ = model.transcribe(str(audio_file), language="uk")
                # Consume generator
                _ = list(segments)
                t1 = time.perf_counter()
                latencies.append(t1 - t0)

            latencies_sorted = sorted(latencies)
            p95_idx = max(0, int(len(latencies) * 0.95) - 1)
            return {
                "measured": True,
                "latency_mean_s": round(statistics.mean(latencies), 3),
                "latency_p95_s": round(latencies_sorted[p95_idx], 3),
                "model": model_size,
            }
        except Exception as exc:
            logger.warning("faster-whisper benchmark failed: %s", exc)
            return {
                "measured": False,
                "latency_mean_s": None,
                "latency_p95_s": None,
                "model": model_size,
                "note": f"benchmark failed: {exc}",
            }

    def benchmark_hailo_whisper(
        self,
        audio_path: str,
        n_runs: int = 10,
    ) -> dict[str, Any]:
        """Time Hailo Whisper transcription; not-measured when hardware absent."""
        try:
            import os

            from hub.edge.voice.hailo_whisper import (  # noqa: I001
                HAILO_AVAILABLE,
                HailoWhisperBackend,
                get_backend,
            )

            if not HAILO_AVAILABLE:
                return {
                    "measured": False,
                    "latency_mean_s": None,
                    "latency_p95_s": None,
                    "note": "hailo_platform not available — run on RPi5 with Hailo-8",
                }

            import asyncio

            os.environ["STT_BACKEND"] = "hailo"
            backend = get_backend(force_cpu=False)
            if not isinstance(backend, HailoWhisperBackend):
                return {
                    "measured": False,
                    "latency_mean_s": None,
                    "latency_p95_s": None,
                    "note": "STT did not land on the NPU (Hailo Whisper assets missing)",
                }
            audio_bytes = Path(audio_path).read_bytes()
            latencies: list[float] = []

            for _ in range(n_runs):
                t0 = time.perf_counter()
                asyncio.get_event_loop().run_until_complete(backend.transcribe(audio_bytes))
                t1 = time.perf_counter()
                latencies.append(t1 - t0)

            latencies_sorted = sorted(latencies)
            p95_idx = max(0, int(len(latencies) * 0.95) - 1)
            return {
                "measured": True,
                "latency_mean_s": round(statistics.mean(latencies), 3),
                "latency_p95_s": round(latencies_sorted[p95_idx], 3),
            }
        except Exception as exc:
            logger.info("Hailo Whisper benchmark failed (%s)", exc)
            return {
                "measured": False,
                "latency_mean_s": None,
                "latency_p95_s": None,
                "note": f"Hailo Whisper unavailable: {exc}",
            }

    def run(self, audio_path: str | None = None) -> dict[str, Any]:
        """Run both benchmarks and return comparison dict."""
        # Prepare audio file
        tmp_file: str | None = None
        if audio_path and Path(audio_path).exists():
            wav_path = audio_path
        else:
            # Generate sine wave
            wav_bytes = _generate_sine_wav()
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.write(wav_bytes)
            tmp.flush()
            tmp.close()
            wav_path = tmp.name
            tmp_file = tmp.name
            logger.info("Generated synthetic 5s sine wave audio: %s", wav_path)

        synthetic_audio = tmp_file is not None
        try:
            fw_result = self.benchmark_faster_whisper(wav_path)
            hailo_result = self.benchmark_hailo_whisper(wav_path)
        finally:
            if tmp_file:
                import os

                try:
                    os.unlink(tmp_file)
                except OSError:
                    pass

        # Speedup only when both engines actually measured a number.
        fw_mean = fw_result.get("latency_mean_s")
        hailo_mean = hailo_result.get("latency_mean_s")
        speedup: float | None = None
        if fw_mean and hailo_mean and hailo_mean > 0:
            speedup = round(fw_mean / hailo_mean, 2)

        return {
            "faster_whisper": fw_result,
            "hailo_whisper": hailo_result,
            "speedup": speedup,
            "synthetic_audio": synthetic_audio,
            "note": (
                (
                    "latency on a synthetic sine wave — representative of compute time, "
                    "not of real-speech decoding"
                )
                if synthetic_audio
                else None
            ),
        }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="STT latency benchmark")
    parser.add_argument("--audio", default=None, help="Path to .wav audio file (5s)")
    parser.add_argument("--model-size", default="small", help="faster-whisper model size")
    parser.add_argument("--n-runs", type=int, default=10)
    parser.add_argument("--output", default="materials/evaluation_results")
    args = parser.parse_args()

    bench = STTBenchmark()
    result = bench.run(audio_path=args.audio)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "stt_latency.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    logger.info("Results written to %s", out_file)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

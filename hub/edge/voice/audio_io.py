"""Audio I/O adapters: local sounddevice and RTSP (camera mic/speaker via ffmpeg)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncGenerator

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_MS = 32
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_SAMPLES * 2  # int16 = 2 bytes


async def local_mic_stream(device_index: int | None = None) -> AsyncGenerator[bytes, None]:
    """Yield raw 16kHz int16 mono chunks from a local sounddevice input."""
    try:
        import sounddevice as sd  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("sounddevice not installed") from exc

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)

    def _cb(indata: object, frames: int, time_info: object, status: object) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, bytes(indata))  # type: ignore[arg-type]

    with sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SAMPLES,
        dtype="int16",
        channels=1,
        device=device_index,
        callback=_cb,
    ):
        while True:
            yield await queue.get()


async def rtsp_mic_stream(rtsp_url: str) -> AsyncGenerator[bytes, None]:
    """Yield raw 16kHz int16 mono chunks decoded from an RTSP audio track via ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found — install it in the voice container")

    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-vn",  # drop video
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    logger.info("RTSP audio source started: %s", rtsp_url)
    try:
        while True:
            chunk = await proc.stdout.read(CHUNK_BYTES)  # type: ignore[union-attr]
            if not chunk:
                logger.warning("RTSP audio stream ended: %s", rtsp_url)
                break
            yield chunk
    finally:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


async def local_speaker_play(audio_pcm: bytes, device_index: int | None = None) -> None:
    """Play raw 16kHz int16 mono PCM through a local sounddevice output."""
    try:
        import numpy as np
        import sounddevice as sd  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("sounddevice/numpy not installed") from exc

    arr = np.frombuffer(audio_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: sd.play(arr, samplerate=SAMPLE_RATE, device=device_index, blocking=True),
    )


async def rtsp_speaker_play(audio_pcm: bytes, rtsp_url: str) -> None:
    """Push raw 16kHz int16 mono PCM to a camera's RTSP back-channel via ffmpeg.

    Reolink E1 Pro supports RTSP back-channel audio; ffmpeg negotiates the
    ANNOUNCE method automatically when the target URL is the camera's stream.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found")

    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-f",
        "s16le",
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-i",
        "pipe:0",
        "-c:a",
        "aac",
        "-b:a",
        "32k",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        proc.stdin.write(audio_pcm)  # type: ignore[union-attr]
        await proc.stdin.drain()  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
        await proc.wait()
    except Exception:
        proc.kill()
        await proc.wait()
        raise

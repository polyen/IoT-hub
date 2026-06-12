"""Full voice command pipeline.

Chain: SileroVAD -> WakeWordDetector -> collect speech -> CPU STT -> MQTT publish.

STT runs on the CPU by default (faster-whisper int8, language="uk"), keeping the
Hailo-8 NPU dedicated to the CV cascade. Set MOONSHINE_MODEL to use a Moonshine
ONNX model instead (English-only in the bundled package — see moonshine_stt.py).
Hailo Whisper on the NPU is opt-in via STT_BACKEND=hailo; then NPU_STRATEGY and the
NPUScheduler coordinate contention with the CV cascade. FORCE_CPU_STT=true forces
CPU even when STT_BACKEND=hailo. Backend selection lives in hailo_whisper.get_backend.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any

import aiomqtt

from hub.edge.voice.audio_io import CHUNK_MS
from hub.edge.voice.hailo_whisper import HailoWhisperBackend, STTBackend, get_backend
from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy
from hub.edge.voice.vad import SileroVAD
from hub.edge.voice.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)

SILENCE_TIMEOUT_SEC = 0.8
MAX_RECORD_SEC = 8.0
# Pre-roll captured before wake-word fires so the first ~500 ms of the command
# (which overlaps with / immediately follows the wake-word) is not lost.
PREROLL_MS = 500
PREROLL_CHUNKS = max(1, PREROLL_MS // CHUNK_MS)
MQTT_TOPIC = "voice/command"

# Width of one producer chunk in bytes (int16 mono @ 16 kHz, CHUNK_MS).
_CHUNK_BYTES = 16000 * CHUNK_MS // 1000 * 2


def _stt_npu_guard(stt: STTBackend, scheduler: NPUScheduler) -> AbstractAsyncContextManager[None]:
    """Coordinate NPU access only when STT actually runs on the Hailo NPU.

    CPU backends (faster-whisper / Moonshine) don't touch the NPU, so they must
    not wait on the CV scheduler — under WHISPER_WAITS that would needlessly
    delay transcription until a CV inter-frame gap. Only the Hailo backend
    contends with the CV cascade.
    """
    if isinstance(stt, HailoWhisperBackend):
        return scheduler.whisper_inference()
    return nullcontext()


def _trim_trailing_silence(audio: bytes, vad: SileroVAD) -> bytes:
    """Drop the silence tail so Whisper doesn't spend tokens hallucinating on it.

    Walks back chunk-by-chunk from the end and stops at the last speech-positive
    chunk. Keeps one chunk of breathing room. No-op if everything is silence.
    """
    if len(audio) <= _CHUNK_BYTES:
        return audio
    # Snapshot then restore VAD LSTM state so trimming doesn't poison live state.
    h_save, c_save = vad._h, vad._c
    try:
        last_speech_end = 0
        for offset in range(0, len(audio) - _CHUNK_BYTES + 1, _CHUNK_BYTES):
            if vad.is_speech(audio[offset : offset + _CHUNK_BYTES]):
                last_speech_end = offset + _CHUNK_BYTES
    finally:
        vad._h, vad._c = h_save, c_save
    if last_speech_end == 0:
        return audio
    # Keep one extra chunk of trailing silence so words aren't clipped mid-phoneme.
    keep = min(len(audio), last_speech_end + _CHUNK_BYTES)
    return audio[:keep]


async def run_pipeline(
    mqtt_host: str = "mosquitto",
    mqtt_port: int = 1883,
    wake_word_model: str | None = None,
    force_cpu: bool = False,
    npu_strategy: NPUStrategy = NPUStrategy.WHISPER_WAITS,
    moonshine_model: str | None = None,
) -> None:
    """Mic-only pipeline loop (no PTT). Use run_pipeline_with_ptt for full stack."""
    vad = SileroVAD()
    vad.load()
    wwd = WakeWordDetector(model_path=wake_word_model)
    wwd.load()
    stt = get_backend(force_cpu=force_cpu, moonshine_model=moonshine_model)
    scheduler = NPUScheduler(strategy=npu_strategy)
    logger.info(
        "Voice pipeline ready — backend=%s strategy=%s", type(stt).__name__, npu_strategy.value
    )
    async with aiomqtt.Client(mqtt_host, mqtt_port) as mqtt:
        await _run_mic_loop(vad, wwd, stt, scheduler, mqtt, redis_url="redis://redis:6379")


async def run_ptt_consumer(
    stt: STTBackend,
    scheduler: NPUScheduler,
    mqtt: aiomqtt.Client,
    redis_url: str = "redis://redis:6379",
) -> None:
    """Poll voice:audio_stream for PTT blobs submitted via /api/agent/voice/audio.

    Each entry carries a ``blob_key`` pointing to the raw audio bytes stored in
    Redis.  We transcribe and publish to the same MQTT topic as the mic pipeline
    so the agent orchestrator receives it identically.
    """
    import redis.asyncio as aioredis

    redis_client = await aioredis.from_url(redis_url, decode_responses=False)
    stream_key = "voice:audio_stream"
    consumer_group = "voice-pipeline"
    consumer_name = "ptt-worker"

    # Create consumer group (ignore if already exists)
    try:
        await redis_client.xgroup_create(stream_key, consumer_group, id="$", mkstream=True)
    except Exception:
        pass

    # Reclaim messages stuck in PEL from a previous run that was interrupted
    # (e.g. task cancellation prevented xack from completing).
    try:
        await redis_client.xautoclaim(
            stream_key,
            consumer_group,
            consumer_name,
            min_idle_time=30_000,
            start_id="0-0",
            count=100,
        )
        logger.info("PTT xautoclaim: reclaimed stale PEL entries")
    except Exception:
        pass  # Redis < 6.2 or empty stream — not fatal

    logger.info("PTT consumer started — polling %s", stream_key)
    try:
        while True:
            try:
                entries: list[Any] = await redis_client.xreadgroup(
                    consumer_group,
                    consumer_name,
                    {stream_key: ">"},
                    count=1,
                    block=500,
                )
            except Exception:
                logger.exception("PTT stream read error — retrying")
                await asyncio.sleep(2)
                continue

            if not entries:
                continue

            for _stream, messages in entries:
                for msg_id, fields in messages:
                    blob_key: bytes | None = fields.get(b"blob_key")
                    if blob_key is None:
                        await redis_client.xack(stream_key, consumer_group, msg_id)
                        continue
                    try:
                        audio_bytes: bytes | None = await redis_client.get(blob_key)
                        if not audio_bytes:
                            logger.warning("PTT blob expired or missing: %s", blob_key)
                            # blob gone — still ack so it doesn't stay in PEL
                        else:
                            async with _stt_npu_guard(stt, scheduler):
                                text = await stt.transcribe(audio_bytes)
                            logger.info("PTT transcribed: %s", text)
                            payload = {"text": text, "tier": 1, "source": "ptt"}
                            await mqtt.publish(MQTT_TOPIC, json.dumps(payload))
                    except aiomqtt.MqttError:
                        # MQTT-level failures (disconnect, broker restart) must
                        # bubble up so the outer reconnect loop spins a fresh
                        # client.  Swallowing them leaves the consumer publishing
                        # into a permanently-disconnected client.
                        logger.warning("PTT publish failed — MQTT disconnected, restarting")
                        # ack the message so we don't re-process on reconnect
                        try:
                            await asyncio.shield(
                                redis_client.xack(stream_key, consumer_group, msg_id)
                            )
                        except Exception:
                            pass
                        if blob_key:
                            try:
                                await asyncio.shield(redis_client.delete(blob_key))
                            except Exception:
                                pass
                        raise
                    except Exception:
                        logger.exception("PTT transcription failed for %s", blob_key)
                    finally:
                        try:
                            await asyncio.shield(
                                redis_client.xack(stream_key, consumer_group, msg_id)
                            )
                        except Exception:
                            pass
                        if blob_key:
                            try:
                                await asyncio.shield(redis_client.delete(blob_key))
                            except Exception:
                                pass
    finally:
        await redis_client.aclose()


async def run_tts_responder(
    redis_url: str = "redis://redis:6379",
) -> None:
    """Subscribe to agent:result and speak responses through the configured output device."""
    import redis.asyncio as aioredis

    from hub.edge.voice.audio_io import local_speaker_play, rtsp_speaker_play
    from hub.edge.voice.tts import synthesize

    redis_client = await aioredis.from_url(redis_url, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("agent:result")
    logger.info("TTS responder started — listening on agent:result")
    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            try:
                data = json.loads(msg["data"])
            except Exception:
                continue
            # Skip only ERROR — those carry exception messages not user text.
            # DENY now carries a UA-rendered text (see orchestrator + i18n_uk.render_deny).
            if data.get("action_class") == "ERROR":
                continue
            text: str = data.get("text", "").strip()
            if not text:
                continue
            try:
                output_id: str | None = await redis_client.get("audio:output_device")
                pcm = await synthesize(text)
                rtsp_failed = False
                if output_id and output_id.startswith("camera:spk:"):
                    camera_id = output_id.split(":", 2)[2]
                    rtsp_url = await _resolve_camera_rtsp(camera_id, redis_url)
                    if rtsp_url:
                        try:
                            await rtsp_speaker_play(pcm, rtsp_url)
                            continue
                        except Exception as exc:
                            # Many cameras advertise RTSP but don't accept the
                            # back-channel ANNOUNCE method (Reolink E1 Pro, most
                            # PoE bullets). Fall back to local speaker instead
                            # of looping the error every agent reply.
                            rtsp_failed = True
                            logger.warning(
                                "RTSP back-channel rejected by %s (%s) — using local speaker",
                                camera_id,
                                type(exc).__name__,
                            )
                    else:
                        logger.warning(
                            "Could not resolve RTSP for speaker %s — using local", camera_id
                        )
                spk_idx: int | None = None
                if output_id and output_id.startswith("local:spk:"):
                    try:
                        spk_idx = int(output_id.split(":")[-1])
                    except ValueError:
                        pass
                await local_speaker_play(pcm, spk_idx)
                if rtsp_failed:
                    logger.info("TTS played on local speaker (RTSP back-channel unavailable)")
            except Exception as exc:
                if "querying device" in str(exc).lower() or "portaudio" in str(exc).lower():
                    logger.debug("TTS skipped — no audio output device")
                else:
                    logger.exception("TTS playback failed for: %r", text)
    finally:
        await pubsub.aclose()
        await redis_client.aclose()


async def run_pipeline_with_ptt(
    mqtt_host: str = "mosquitto",
    mqtt_port: int = 1883,
    wake_word_model: str | None = None,
    force_cpu: bool = False,
    npu_strategy: NPUStrategy = NPUStrategy.WHISPER_WAITS,
    moonshine_model: str | None = None,
    redis_url: str = "redis://redis:6379",
) -> None:
    """Run mic pipeline, PTT consumer, and TTS responder concurrently.

    The MQTT client lives inside a reconnect loop — if mosquitto restarts or the
    keepalive expires, child tasks raise aiomqtt.MqttError which bubbles up via
    ``FIRST_EXCEPTION``; the outer loop re-opens the client and re-spawns tasks.
    Without this, every publish after a single disconnect fails with code 4.
    """
    vad = SileroVAD()
    vad.load()
    wwd = WakeWordDetector(model_path=wake_word_model)
    wwd.load()
    stt = get_backend(force_cpu=force_cpu, moonshine_model=moonshine_model)
    scheduler = NPUScheduler(strategy=npu_strategy)

    logger.info("Voice pipeline (mic+PTT+TTS) ready — backend=%s", type(stt).__name__)

    while True:
        try:
            async with aiomqtt.Client(mqtt_host, mqtt_port) as mqtt:
                mic_task = asyncio.create_task(
                    _run_mic_loop(vad, wwd, stt, scheduler, mqtt, redis_url), name="voice-mic"
                )
                ptt_task = asyncio.create_task(
                    run_ptt_consumer(stt, scheduler, mqtt, redis_url), name="voice-ptt"
                )
                tts_task = asyncio.create_task(run_tts_responder(redis_url), name="voice-tts")
                done, pending = await asyncio.wait(
                    [mic_task, ptt_task, tts_task], return_when=asyncio.FIRST_EXCEPTION
                )
                for task in pending:
                    task.cancel()
                # Wait for cancellations so tasks don't leak across reconnects
                for task in pending:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                for task in done:
                    exc = task.exception()
                    if exc is not None:
                        raise exc
        except aiomqtt.MqttError as exc:
            logger.warning("Voice MQTT lost: %s — reconnecting in 5s", exc)
            await asyncio.sleep(5)


async def _audio_stream_for_device(
    device_id: str | None,
    redis_url: str,
) -> AsyncGenerator[bytes, None]:
    """Return the right audio generator based on the active input device id.

    device_id format:
      "local:mic:<index>"   → sounddevice input
      "camera:mic:<uuid>"   → RTSP audio from camera (fetched via backend API)
      None / unknown        → local mic index 0
    """
    from hub.edge.voice.audio_io import local_mic_stream, rtsp_mic_stream

    if device_id and device_id.startswith("camera:mic:"):
        camera_id = device_id.split(":", 2)[2]
        # Resolve RTSP URL from backend
        rtsp_url = await _resolve_camera_rtsp(camera_id, redis_url)
        if rtsp_url:
            logger.info("Audio source: RTSP camera %s (%s)", camera_id, rtsp_url)
            async for chunk in rtsp_mic_stream(rtsp_url):
                yield chunk
            return
        logger.warning(
            "Could not resolve RTSP URL for camera %s — falling back to local mic", camera_id
        )

    # Default: local sounddevice
    idx: int | None = None
    if device_id and device_id.startswith("local:mic:"):
        try:
            idx = int(device_id.split(":")[-1])
        except ValueError:
            pass
    logger.info("Audio source: local mic (device=%s)", idx)
    async for chunk in local_mic_stream(idx):
        yield chunk


async def _resolve_camera_rtsp(camera_id: str, redis_url: str) -> str | None:
    """Look up the RTSP URL for a camera placement from the backend DB."""
    try:
        from sqlalchemy import select

        from hub.backend.db import AsyncSessionLocal
        from hub.backend.models import DevicePlacement

        async with AsyncSessionLocal() as session:
            import uuid as _uuid

            res = await session.execute(
                select(DevicePlacement).where(DevicePlacement.id == _uuid.UUID(camera_id)).limit(1)
            )
            p = res.scalar_one_or_none()
            if not p:
                logger.warning("Camera placement %s not found in DB", camera_id)
                return None
            cfg = p.config or {}
            url = cfg.get("rtsp_url") or cfg.get("stream_rtsp")
            if not url:
                logger.warning(
                    "Camera %s has no rtsp_url / stream_rtsp in placement.config (keys: %s)",
                    camera_id,
                    list(cfg.keys()),
                )
            return url
    except Exception as exc:
        # Most common cause on host systemd: DATABASE_URL env var missing or DB
        # unreachable from the host (Postgres in Docker, host can't dial it).
        logger.warning(
            "RTSP resolve failed for camera %s (%s: %s) — set DATABASE_URL?",
            camera_id,
            type(exc).__name__,
            exc,
        )
    return None


async def _run_mic_loop(
    vad: SileroVAD,
    wwd: WakeWordDetector,
    stt: STTBackend,
    scheduler: NPUScheduler,
    mqtt: aiomqtt.Client,
    redis_url: str = "redis://redis:6379",
) -> None:
    """Mic loop that restarts whenever the active audio device changes in Redis."""
    import redis.asyncio as aioredis

    redis_client = await aioredis.from_url(redis_url, decode_responses=True)

    async def _get_device() -> str | None:
        val = await redis_client.get("audio:input_device")
        return None if val is None else str(val)

    try:
        while True:
            device_id = await _get_device()
            logger.info("Starting mic loop with device: %s", device_id or "local:mic:0")

            # Run the listen loop; cancel it when device changes
            changed = asyncio.Event()

            async def _watch_config(_changed: asyncio.Event = changed) -> None:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe("audio:config_changed")
                async for msg in pubsub.listen():
                    if msg["type"] == "message":
                        _changed.set()
                        break
                await pubsub.aclose()

            watch_task = asyncio.create_task(_watch_config())
            listen_task = asyncio.create_task(
                _listen_with_device(device_id, vad, wwd, stt, scheduler, mqtt, redis_url)
            )

            done, pending = await asyncio.wait(
                [listen_task, watch_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()

            if listen_task in done and listen_task.exception():
                exc = listen_task.exception()
                # MQTT errors must propagate so run_pipeline_with_ptt reopens the
                # client.  Without this we'd retry the mic loop forever against a
                # dead aiomqtt client.
                if isinstance(exc, aiomqtt.MqttError):
                    raise exc
                # Persistent failures (missing libportaudio, no device, missing
                # input device) won't fix themselves between iterations — back off
                # long to avoid flooding the journal. PTT/TTS paths are unaffected.
                exc_str = str(exc).lower()
                persistent = (
                    "portaudio" in exc_str or "querying device" in exc_str or "no device" in exc_str
                )
                delay = 60 if persistent else 3
                logger.error("Mic loop error: %s — restarting in %ds", exc, delay)
                await asyncio.sleep(delay)
    finally:
        await redis_client.aclose()


async def _listen_with_device(
    device_id: str | None,
    vad: SileroVAD,
    wwd: WakeWordDetector,
    stt: STTBackend,
    scheduler: NPUScheduler,
    mqtt: aiomqtt.Client,
    redis_url: str,
) -> None:
    """Single-stream listen loop.

    One audio source is opened once; both wake-word detection and post-wake
    collection consume from it. A circular pre-roll buffer keeps the last
    PREROLL_MS of audio so the command head (which overlaps with the wake-word)
    is not clipped. After STT, the loop returns to wake-word mode on the same
    stream — no second ffmpeg/sounddevice open.
    """
    preroll: collections.deque[bytes] = collections.deque(maxlen=PREROLL_CHUNKS)
    loop = asyncio.get_event_loop()

    # State machine: "listen" → wake-word watch; "collect" → record until silence
    state = "listen"
    collected: list[bytes] = []
    collect_start = 0.0
    last_speech = 0.0

    async for chunk in _audio_stream_for_device(device_id, redis_url):
        if state == "listen":
            preroll.append(chunk)
            if not wwd.detect(chunk):
                continue
            logger.info("Wake word detected — recording command")
            collected = list(preroll)
            preroll.clear()
            collect_start = loop.time()
            last_speech = collect_start
            state = "collect"
            continue

        # state == "collect"
        collected.append(chunk)
        now = loop.time()
        if vad.is_speech(chunk):
            last_speech = now

        done = (now - last_speech > SILENCE_TIMEOUT_SEC) or (now - collect_start > MAX_RECORD_SEC)
        if not done:
            continue

        audio = _trim_trailing_silence(b"".join(collected), vad)
        try:
            async with _stt_npu_guard(stt, scheduler):
                text = await stt.transcribe(audio)
            logger.info("Transcribed (%d B): %s", len(audio), text)
            if text:
                payload = {"text": text, "tier": 1}
                await mqtt.publish(MQTT_TOPIC, json.dumps(payload))
        except aiomqtt.MqttError:
            # Bubble MQTT failures up so the outer reconnect loop restarts.
            logger.warning("Mic-loop publish failed — MQTT disconnected, restarting")
            wwd.reset()
            vad._reset_states()
            raise
        except Exception:
            logger.exception("STT/publish error — continuing")
        finally:
            wwd.reset()
            vad._reset_states()
            collected = []
            state = "listen"


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    _strategy_map = {s.value: s for s in NPUStrategy}

    asyncio.run(
        run_pipeline_with_ptt(
            mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            wake_word_model=os.environ.get("WAKE_WORD_MODEL_PATH") or None,
            force_cpu=os.environ.get("FORCE_CPU_STT", "false").lower() == "true",
            npu_strategy=_strategy_map.get(
                os.environ.get("NPU_STRATEGY", "whisper_waits"), NPUStrategy.WHISPER_WAITS
            ),
            moonshine_model=os.environ.get("MOONSHINE_MODEL") or None,
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379"),
        )
    )

"""Full voice command pipeline.

Chain: SileroVAD -> WakeWordDetector -> collect speech -> Moonshine/Hailo/CPU STT -> MQTT publish.

Primary STT: Moonshine ONNX (UsefulSensors/moonshine-tiny-uk) — set MOONSHINE_MODEL to override.
NPU scheduling: when WHISPER_HEF_PATH is set and Moonshine is unavailable, Whisper encoder
shares the Hailo NPU with the CV cascade.  NPU_STRATEGY controls the contention policy.
Set FORCE_CPU_STT=true to bypass Hailo entirely and fall back to faster-whisper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import aiomqtt

from hub.edge.voice.hailo_whisper import STTBackend, get_backend
from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy
from hub.edge.voice.vad import SileroVAD
from hub.edge.voice.wake_word import WakeWordDetector

logger = logging.getLogger(__name__)

SILENCE_TIMEOUT_SEC = 2.0
MAX_RECORD_SEC = 15.0
MQTT_TOPIC = "voice/command"


async def _collect_speech(
    vad: SileroVAD,
    audio_source: AsyncGenerator[bytes, None],
) -> bytes:
    """Collect audio chunks until silence or max duration."""
    chunks: list[bytes] = []
    start = asyncio.get_event_loop().time()
    last_speech = start

    async for chunk in audio_source:
        chunks.append(chunk)
        now = asyncio.get_event_loop().time()

        if vad.is_speech(chunk):
            last_speech = now

        if now - last_speech > SILENCE_TIMEOUT_SEC:
            break
        if now - start > MAX_RECORD_SEC:
            break

    return b"".join(chunks)


async def run_pipeline(
    mqtt_host: str = "mosquitto",
    mqtt_port: int = 1883,
    wake_word_model: str | None = None,
    force_cpu: bool = False,
    hef_path: Path | None = None,
    npu_strategy: NPUStrategy = NPUStrategy.WHISPER_WAITS,
    moonshine_model: str | None = None,
) -> None:
    """Mic-only pipeline loop (no PTT). Use run_pipeline_with_ptt for full stack."""
    vad = SileroVAD()
    vad.load()
    wwd = WakeWordDetector(model_path=wake_word_model)
    wwd.load()
    stt = get_backend(hef_path=hef_path, force_cpu=force_cpu, moonshine_model=moonshine_model)
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

    logger.info("PTT consumer started — polling %s", stream_key)
    try:
        while True:
            try:
                entries: list[Any] = await redis_client.xreadgroup(
                    consumer_group,
                    consumer_name,
                    {stream_key: ">"},
                    count=1,
                    block=5000,
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
                            continue
                        async with scheduler.whisper_inference():
                            text = await stt.transcribe(audio_bytes)
                        logger.info("PTT transcribed: %s", text)
                        payload = {"text": text, "tier": 1, "source": "ptt"}
                        await mqtt.publish(MQTT_TOPIC, json.dumps(payload))
                    except Exception:
                        logger.exception("PTT transcription failed for %s", blob_key)
                    finally:
                        await redis_client.xack(stream_key, consumer_group, msg_id)
                        if blob_key:
                            await redis_client.delete(blob_key)
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
            # Skip DENY/ERROR results — these contain internal policy names, not user text
            if data.get("action_class") in ("DENY", "ERROR"):
                continue
            text: str = data.get("text", "").strip()
            if not text:
                continue
            try:
                output_id: str | None = await redis_client.get("audio:output_device")
                pcm = await synthesize(text)
                if output_id and output_id.startswith("camera:spk:"):
                    camera_id = output_id.split(":", 2)[2]
                    rtsp_url = await _resolve_camera_rtsp(camera_id, redis_url)
                    if rtsp_url:
                        await rtsp_speaker_play(pcm, rtsp_url)
                        continue
                    logger.warning("Could not resolve RTSP for speaker %s — using local", camera_id)
                spk_idx: int | None = None
                if output_id and output_id.startswith("local:spk:"):
                    try:
                        spk_idx = int(output_id.split(":")[-1])
                    except ValueError:
                        pass
                await local_speaker_play(pcm, spk_idx)
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
    hef_path: Path | None = None,
    npu_strategy: NPUStrategy = NPUStrategy.WHISPER_WAITS,
    moonshine_model: str | None = None,
    redis_url: str = "redis://redis:6379",
) -> None:
    """Run mic pipeline, PTT consumer, and TTS responder concurrently."""
    vad = SileroVAD()
    vad.load()
    wwd = WakeWordDetector(model_path=wake_word_model)
    wwd.load()
    stt = get_backend(hef_path=hef_path, force_cpu=force_cpu, moonshine_model=moonshine_model)
    scheduler = NPUScheduler(strategy=npu_strategy)

    logger.info("Voice pipeline (mic+PTT+TTS) ready — backend=%s", type(stt).__name__)

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
        for task in done:
            if task.exception():
                raise task.exception()  # type: ignore[misc]


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
    """Look up the RTSP URL for a camera placement from Redis cache or backend DB."""
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
            if p:
                cfg = p.config or {}
                return cfg.get("rtsp_url") or cfg.get("stream_rtsp")
    except Exception as exc:
        logger.debug("RTSP resolve failed for %s: %s", camera_id, exc)
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
        return await redis_client.get("audio:input_device")

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
                # Device-not-found is a persistent condition; back off longer to
                # avoid log spam. PTT path is unaffected and continues to work.
                delay = (
                    30
                    if "querying device" in str(exc).lower() or "no device" in str(exc).lower()
                    else 3
                )
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
    stream = _audio_stream_for_device(device_id, redis_url)
    async for _ in wwd.listen(vad.filter_stream(stream)):
        logger.info("Wake word detected — recording command")
        try:
            audio = await asyncio.wait_for(
                _collect_speech(vad, _audio_stream_for_device(device_id, redis_url)),
                timeout=MAX_RECORD_SEC + 2,
            )
            async with scheduler.whisper_inference():
                text = await stt.transcribe(audio)
            logger.info("Transcribed: %s", text)
            payload = {"text": text, "tier": 1}
            await mqtt.publish(MQTT_TOPIC, json.dumps(payload))
        except Exception:
            logger.exception("Pipeline error — continuing")


if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    _strategy_map = {s.value: s for s in NPUStrategy}
    _hef_env = os.environ.get("WHISPER_HEF_PATH")

    asyncio.run(
        run_pipeline_with_ptt(
            mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            wake_word_model=os.environ.get("WAKE_WORD_MODEL_PATH") or None,
            force_cpu=os.environ.get("FORCE_CPU_STT", "false").lower() == "true",
            hef_path=Path(_hef_env) if _hef_env else None,
            npu_strategy=_strategy_map.get(
                os.environ.get("NPU_STRATEGY", "whisper_waits"), NPUStrategy.WHISPER_WAITS
            ),
            moonshine_model=os.environ.get("MOONSHINE_MODEL") or None,
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379"),
        )
    )

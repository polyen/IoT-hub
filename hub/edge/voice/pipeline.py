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
        await _run_mic_loop(vad, wwd, stt, scheduler, mqtt)


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
    """Run mic pipeline and PTT consumer concurrently."""
    vad = SileroVAD()
    vad.load()
    wwd = WakeWordDetector(model_path=wake_word_model)
    wwd.load()
    stt = get_backend(hef_path=hef_path, force_cpu=force_cpu, moonshine_model=moonshine_model)
    scheduler = NPUScheduler(strategy=npu_strategy)

    logger.info("Voice pipeline (mic+PTT) ready — backend=%s", type(stt).__name__)

    async with aiomqtt.Client(mqtt_host, mqtt_port) as mqtt:
        mic_task = asyncio.create_task(
            _run_mic_loop(vad, wwd, stt, scheduler, mqtt), name="voice-mic"
        )
        ptt_task = asyncio.create_task(
            run_ptt_consumer(stt, scheduler, mqtt, redis_url), name="voice-ptt"
        )
        done, pending = await asyncio.wait(
            [mic_task, ptt_task], return_when=asyncio.FIRST_EXCEPTION
        )
        for task in pending:
            task.cancel()
        for task in done:
            if task.exception():
                raise task.exception()  # type: ignore[misc]


async def _run_mic_loop(
    vad: SileroVAD,
    wwd: WakeWordDetector,
    stt: STTBackend,
    scheduler: NPUScheduler,
    mqtt: aiomqtt.Client,
) -> None:
    async for _ in wwd.listen(vad.stream()):
        logger.info("Wake word detected — recording command")
        try:
            audio = await asyncio.wait_for(
                _collect_speech(vad, vad.stream()),
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

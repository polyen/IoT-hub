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

import aiomqtt

from hub.edge.voice.hailo_whisper import get_backend
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
    """Main voice pipeline loop. Runs until cancelled."""
    vad = SileroVAD()
    vad.load()

    wwd = WakeWordDetector(model_path=wake_word_model)
    wwd.load()

    stt = get_backend(hef_path=hef_path, force_cpu=force_cpu, moonshine_model=moonshine_model)
    scheduler = NPUScheduler(strategy=npu_strategy)

    logger.info(
        "Voice pipeline ready — backend=%s strategy=%s",
        type(stt).__name__,
        npu_strategy.value,
    )

    async with aiomqtt.Client(mqtt_host, mqtt_port) as mqtt:
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
        run_pipeline(
            mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            wake_word_model=os.environ.get("WAKE_WORD_MODEL_PATH") or None,
            force_cpu=os.environ.get("FORCE_CPU_STT", "false").lower() == "true",
            hef_path=Path(_hef_env) if _hef_env else None,
            npu_strategy=_strategy_map.get(
                os.environ.get("NPU_STRATEGY", "whisper_waits"), NPUStrategy.WHISPER_WAITS
            ),
            moonshine_model=os.environ.get("MOONSHINE_MODEL") or None,
        )
    )

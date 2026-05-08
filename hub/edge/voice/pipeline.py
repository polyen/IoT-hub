"""Full voice command pipeline.

Chain: SileroVAD -> WakeWordDetector -> collect speech -> Hailo/CPU STT -> MQTT publish.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator

import aiomqtt

from hub.edge.voice.hailo_whisper import get_backend
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
) -> None:
    """Main voice pipeline loop. Runs until cancelled."""
    vad = SileroVAD()
    vad.load()

    wwd = WakeWordDetector(model_path=wake_word_model)
    wwd.load()

    stt = get_backend(force_cpu=force_cpu)

    logger.info("Voice pipeline ready — listening for wake word")

    async with aiomqtt.Client(mqtt_host, mqtt_port) as mqtt:
        async for _ in wwd.listen(vad.stream()):
            logger.info("Wake word detected — recording command")
            try:
                audio = await asyncio.wait_for(
                    _collect_speech(vad, vad.stream()),
                    timeout=MAX_RECORD_SEC + 2,
                )
                text = await stt.transcribe(audio)
                logger.info("Transcribed: %s", text)

                payload = {"text": text, "tier": 1}
                await mqtt.publish(MQTT_TOPIC, json.dumps(payload))
            except Exception:
                logger.exception("Pipeline error — continuing")


if __name__ == "__main__":
    asyncio.run(
        run_pipeline(
            mqtt_host=os.environ.get("MQTT_HOST", "mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            wake_word_model=os.environ.get("WAKE_WORD_MODEL_PATH") or None,
            force_cpu=os.environ.get("FORCE_CPU_STT", "false").lower() == "true",
        )
    )

"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from hub.backend import mqtt_subscriber
from hub.backend.config import settings
from hub.backend.db import engine
from hub.backend.routes.agent import router as agent_router
from hub.backend.routes.audio import router as audio_router
from hub.backend.routes.confirm import router as confirm_router
from hub.backend.routes.cv import router as cv_router
from hub.backend.routes.deploy import router as deploy_router
from hub.backend.routes.devices import router as devices_router
from hub.backend.routes.digest import router as digest_router
from hub.backend.routes.enroll import router as enroll_router
from hub.backend.routes.events import router as events_router
from hub.backend.routes.floorplan import router as floorplan_router
from hub.backend.routes.health import router as health_router
from hub.backend.routes.policy import router as policy_router
from hub.backend.routes.privacy import router as privacy_router
from hub.backend.routes.security import router as security_router
from hub.backend.routes.system import router as system_router
from hub.edge.mlops.deploy import ModelStore, monitor_loop
from hub.edge.storage.t0 import cleanup_old_frames

logger = logging.getLogger(__name__)


async def _feedback_mining_loop(
    redis: aioredis.Redis,
    debounce_sec: int = 1800,
) -> None:
    """Subscribe to feedback:new and batch-trigger dvc repro mine_hard_negatives.

    Collects feedback events for *debounce_sec* after the first arrival, then
    fires a single DVC run so rapid user-labelling doesn't spawn concurrent jobs.
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe("feedback:new")
    pending = 0
    deadline: float | None = None
    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True),
                    timeout=5.0,
                )
            except TimeoutError:
                msg = None
            if msg and msg["type"] == "message":
                pending += 1
                if deadline is None:
                    deadline = loop.time() + debounce_sec
            if deadline is not None and loop.time() >= deadline:
                logger.info("Triggering DVC mining stage (%d pending feedback events)", pending)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "dvc",
                        "repro",
                        "mine_hard_negatives",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await proc.communicate()
                    if proc.returncode != 0:
                        logger.warning(
                            "DVC mining failed (rc=%d): %s", proc.returncode, stderr.decode()
                        )
                    else:
                        logger.info("DVC mining completed")
                except Exception:
                    logger.exception("Failed to spawn DVC mining")
                pending = 0
                deadline = None
    finally:
        await pubsub.unsubscribe("feedback:new")
        await pubsub.aclose()


async def _t0_cleanup_loop(interval_s: int = 86_400) -> None:
    """Daily T0 frame cleanup. Tolerant of missing mount in dev mode."""
    while True:
        try:
            deleted = cleanup_old_frames()
            if deleted:
                logger.info("T0 cleanup: removed %d old frames", deleted)
        except Exception as exc:  # noqa: BLE001
            logger.warning("T0 cleanup skipped: %s", exc)
        await asyncio.sleep(interval_s)


async def _orchestrator_loop(redis_client: aioredis.Redis) -> None:
    """Run agent orchestrator — subscribes to voice/command MQTT and processes intents."""
    import aiomqtt

    from hub.backend.db import AsyncSessionLocal
    from hub.edge.agent.llm_local import LocalLLMClient
    from hub.edge.agent.orchestrator import AgentOrchestrator
    from hub.edge.agent.policy import PolicyEngine
    from hub.edge.agent.router import IntentRouter

    while True:
        try:
            policy = PolicyEngine()
            router = IntentRouter()
            router.load()
            llm = LocalLLMClient(base_url=settings.llm_url)
            mqtt_client = aiomqtt.Client(settings.mqtt_host, settings.mqtt_port)
            orchestrator = AgentOrchestrator(
                policy=policy,
                router=router,
                llm=llm,
                redis_client=redis_client,
                mqtt_client=mqtt_client,
                session_factory=AsyncSessionLocal,
            )
            await orchestrator.run()
        except Exception as exc:
            logger.error("Orchestrator crashed: %s — restarting in 5s", exc, exc_info=True)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(mqtt_subscriber.run(app.state.redis), name="mqtt-subscriber"),
        asyncio.create_task(mqtt_subscriber.run_outbound(app.state.redis), name="mqtt-outbound"),
    ]
    if os.environ.get("ENABLE_DEPLOY_MONITOR", "true").lower() == "true":
        # Watch every kind that has its own rollback trigger (yolo + pose);
        # face/whisper currently lack class-rate metrics so they are managed
        # manually via the deploy API.
        stores = {
            "yolo": ModelStore(kind="yolo"),
            "pose": ModelStore(kind="pose"),
        }
        tasks.append(
            asyncio.create_task(
                monitor_loop(
                    stores,
                    interval=int(os.environ.get("DEPLOY_MONITOR_INTERVAL", "300")),
                ),
                name="deploy-monitor",
            )
        )
    if os.environ.get("ENABLE_T0_CLEANUP", "true").lower() == "true":
        tasks.append(asyncio.create_task(_t0_cleanup_loop(), name="t0-cleanup"))
    if os.environ.get("ENABLE_FEEDBACK_MINING", "true").lower() == "true":
        tasks.append(
            asyncio.create_task(
                _feedback_mining_loop(
                    app.state.redis,
                    debounce_sec=int(os.environ.get("FEEDBACK_MINING_DEBOUNCE_SEC", "1800")),
                ),
                name="feedback-mining",
            )
        )
    if os.environ.get("ENABLE_ORCHESTRATOR", "true").lower() == "true":
        tasks.append(
            asyncio.create_task(
                _orchestrator_loop(app.state.redis),
                name="orchestrator",
            )
        )

    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await app.state.redis.aclose()
        await engine.dispose()


app = FastAPI(title="IoT Hub Backend", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(events_router)
app.include_router(deploy_router)
app.include_router(floorplan_router)
app.include_router(confirm_router)
app.include_router(cv_router)
app.include_router(enroll_router)
app.include_router(devices_router)
app.include_router(agent_router)
app.include_router(system_router)
app.include_router(policy_router)
app.include_router(digest_router)
app.include_router(privacy_router)
app.include_router(security_router)
app.include_router(audio_router)
app.mount("/metrics", make_asgi_app())

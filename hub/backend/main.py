"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

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
from hub.backend.routes.sensors import router as sensors_router
from hub.backend.routes.system import router as system_router
from hub.edge.mlops.deploy import ModelStore, monitor_loop
from hub.edge.storage.t0 import cleanup_old_frames

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _feedback_mining_loop(
    redis: aioredis.Redis,
    debounce_sec: int = 1800,
) -> None:
    """Subscribe to feedback:new and batch-trigger dvc repro mine_hard_negatives.

    Collects feedback events for *debounce_sec* after the first arrival, then
    fires a single DVC run so rapid user-labelling doesn't spawn concurrent jobs.
    """
    pubsub: Any = redis.pubsub()
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


async def _orchestrator_loop(
    redis_client: aioredis.Redis,
    device_registry: Any,
) -> None:
    """Run agent orchestrator — subscribes to voice/command MQTT and processes intents."""
    while True:
        try:
            import aiomqtt

            from hub.backend.db import AsyncSessionLocal
            from hub.edge.agent.llm_local import LocalLLMClient
            from hub.edge.agent.llm_reasoning import LLMReasoner
            from hub.edge.agent.orchestrator import AgentOrchestrator
            from hub.edge.agent.policy import PolicyEngine
            from hub.edge.agent.router import IntentRouter
            from hub.edge.agent.state_verifier import StateVerifier

            policy = PolicyEngine()
            policy.load()
            router = IntentRouter()
            router.load()
            llm = LocalLLMClient(base_url=settings.llm_url)
            mqtt_client = aiomqtt.Client(settings.mqtt_host, settings.mqtt_port)

            state_verifier = StateVerifier(redis_client)
            llm_reasoner: LLMReasoner | None = None
            if os.environ.get("LLM_REASONING_ENABLED", "false").lower() == "true":
                llm_reasoner = LLMReasoner(llm=llm, registry=device_registry)

            orchestrator = AgentOrchestrator(
                policy=policy,
                router=router,
                llm=llm,
                redis_client=redis_client,
                mqtt_client=mqtt_client,
                session_factory=AsyncSessionLocal,
                device_registry=device_registry,
                state_verifier=state_verifier,
                llm_reasoner=llm_reasoner,
            )
            logger.info(
                "Orchestrator starting (registry=%s, verifier=on, reasoner=%s)",
                "on" if device_registry is not None else "off",
                "on" if llm_reasoner is not None else "off",
            )
            await orchestrator.run()
        except Exception as exc:
            logger.error("Orchestrator crashed: %s — restarting in 5s", exc, exc_info=True)
            await asyncio.sleep(5)


async def _init_mediamtx_from_db() -> None:
    """Configure mediamtx camera paths from DevicePlacement.config on startup.

    Camera stream URLs live in the DB (set via floor-plan editor), not in env vars.
    This runs once at startup so mediamtx is always in sync with the DB after a
    container restart — no CAMERA_RTSP_URL / CAMERA_RTSP_HD_URL env vars needed.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from hub.backend.db import AsyncSessionLocal  # noqa: PLC0415
    from hub.backend.models import DevicePlacement  # noqa: PLC0415
    from hub.backend.routes.cv import sync_camera_paths_to_mediamtx  # noqa: PLC0415

    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(DevicePlacement).where(DevicePlacement.kind == "camera")
            )
            cameras = [(p.device_id, p.config or {}) for p in res.scalars()]
        if cameras:
            await sync_camera_paths_to_mediamtx(cameras)
            logger.info("mediamtx: initialized %d camera path(s) from DB", len(cameras))
    except Exception as exc:
        logger.warning("mediamtx: startup sync skipped (%s)", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    # Device registry — must load before orchestrator starts
    from hub.backend.db import AsyncSessionLocal  # noqa: PLC0415
    from hub.backend.services.device_registry import DeviceRegistry  # noqa: PLC0415

    device_registry = DeviceRegistry(
        session_factory=AsyncSessionLocal,
        redis_client=app.state.redis,
    )
    try:
        await device_registry.load()
    except Exception as exc:
        logger.warning("DeviceRegistry initial load failed (DB may not be up yet): %s", exc)
    app.state.device_registry = device_registry

    await _init_mediamtx_from_db()

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(mqtt_subscriber.run(app.state.redis), name="mqtt-subscriber"),
        asyncio.create_task(mqtt_subscriber.run_outbound(app.state.redis), name="mqtt-outbound"),
        asyncio.create_task(device_registry.watch(), name="device-registry-watch"),
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
    if os.environ.get("ENABLE_SYSTEM_METRICS", "true").lower() == "true":
        # Best-effort: the System dashboard producer must never take down the
        # backend (e.g. if psutil is somehow missing from the image).
        try:
            from hub.backend.services.system_metrics import metrics_loop  # noqa: PLC0415

            tasks.append(asyncio.create_task(metrics_loop(app.state.redis), name="system-metrics"))
        except Exception as exc:
            logger.warning("system-metrics producer not started: %s", exc)
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
                _orchestrator_loop(app.state.redis, device_registry),
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
app.include_router(sensors_router)
app.include_router(audio_router)
app.mount("/metrics", make_asgi_app())

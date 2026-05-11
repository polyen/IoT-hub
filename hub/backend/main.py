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
from hub.backend.routes.deploy import router as deploy_router
from hub.backend.routes.events import router as events_router
from hub.backend.routes.health import router as health_router
from hub.edge.mlops.deploy import ModelStore, monitor_loop
from hub.edge.storage.t0 import cleanup_old_frames

logger = logging.getLogger(__name__)


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(mqtt_subscriber.run(app.state.redis), name="mqtt-subscriber"),
    ]
    if os.environ.get("ENABLE_DEPLOY_MONITOR", "true").lower() == "true":
        tasks.append(
            asyncio.create_task(
                monitor_loop(
                    ModelStore(), interval=int(os.environ.get("DEPLOY_MONITOR_INTERVAL", "300"))
                ),
                name="deploy-monitor",
            )
        )
    if os.environ.get("ENABLE_T0_CLEANUP", "true").lower() == "true":
        tasks.append(asyncio.create_task(_t0_cleanup_loop(), name="t0-cleanup"))

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
app.mount("/metrics", make_asgi_app())

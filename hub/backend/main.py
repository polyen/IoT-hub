"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from hub.backend import mqtt_subscriber
from hub.backend.config import settings
from hub.backend.db import engine
from hub.backend.routes.events import router as events_router
from hub.backend.routes.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    task = asyncio.create_task(mqtt_subscriber.run(app.state.redis))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await app.state.redis.aclose()
    await engine.dispose()


app = FastAPI(title="IoT Hub Backend", version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(events_router)
app.mount("/metrics", make_asgi_app())

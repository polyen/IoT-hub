"""System-health metrics producer.

`routes/system.py` is a pure *reader* of a set of Redis keys (``system:*``,
``heartbeat:*``, ``models:*``). This module is the *writer*: a background loop
spawned from the backend lifespan that samples the host with ``psutil``, probes
the infra services it shares a process with (redis / postgres) and the MQTT
broker, resolves the active model versions from :class:`ModelStore`, and writes
everything with a short TTL so the dashboard goes "offline" within one
``HEARTBEAT_TTL_SEC`` window when the producer dies.

Heartbeats for the out-of-process edge services are written elsewhere, on their
existing liveness signals, so they need no Redis access added here:
  * ``heartbeat:cv``    — ``routes/cv.py::pipeline_config`` (CV polls it ~5 s).
  * ``heartbeat:voice`` — ``hub/edge/voice/pipeline.py`` (own 10 s loop).
  * ``heartbeat:mqtt``  — ``mqtt_subscriber.run`` (on connect + per message).

All values are a *last-seen* ISO-8601 timestamp; status is simply key-present.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import UTC, datetime
from typing import Any

import psutil
from sqlalchemy import text

logger = logging.getLogger(__name__)

# How often the loop samples, and how long each key survives without a refresh.
# TTL > interval so a single slow cycle doesn't flap services to "offline".
METRICS_INTERVAL_SEC = int(os.environ.get("SYSTEM_METRICS_INTERVAL_SEC", "10"))
HEARTBEAT_TTL_SEC = int(os.environ.get("SYSTEM_METRICS_TTL_SEC", "30"))

# Disk whose free space is shown as "NVMe вільно". On the RPi5 the SSD is the
# data volume; default to the filesystem root which is always present.
NVME_PATH = os.environ.get("NVME_PATH", "/")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_cpu_temp() -> float | None:
    """Best-effort CPU temperature in °C (Linux/RPi only; None elsewhere)."""
    sensors = getattr(psutil, "sensors_temperatures", None)
    if sensors is None:
        return None
    try:
        temps = sensors()
    except Exception:
        return None
    # RPi exposes "cpu_thermal"; fall back to the first reading we find.
    for key in ("cpu_thermal", "coretemp", "k10temp"):
        if temps.get(key):
            return float(temps[key][0].current)
    for readings in temps.values():
        if readings:
            return float(readings[0].current)
    return None


def _read_npu_util() -> float | None:
    """Hailo-8 NPU utilisation, when the platform lib is present. None otherwise.

    Kept import-local so dev machines / CI without ``hailo_platform`` are fine —
    same graceful-degradation contract the cv/voice pipelines follow.
    """
    try:
        from hailo_platform import Device
    except Exception:
        return None
    try:
        # No stable per-process utilisation API across HailoRT versions; presence
        # of a scanned device is reported as 0.0 rather than guessing a number.
        devices = Device.scan()
        return 0.0 if devices else None
    except Exception:
        return None


def _model_versions() -> dict[str, str | None]:
    """Resolve active model stems from the ModelStore symlinks (fs-only, safe)."""
    from hub.edge.mlops.deploy import ModelStore

    def _current(kind: str) -> str | None:
        try:
            return ModelStore(kind=kind).current_version()
        except Exception:
            return None

    return {
        "cv_version": _current("yolo"),
        "whisper_version": _current("whisper") or os.environ.get("STT_BACKEND") or None,
        "llm_version": os.environ.get("LLM_MODEL") or None,
    }


async def _check_postgres() -> bool:
    """SELECT 1 against the app pool. False on any error (DB down / not ready)."""
    from hub.backend.db import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def collect_once(redis: Any) -> None:
    """Sample the host + infra once and write all health keys with a TTL."""
    ts = _now_iso()
    ttl = HEARTBEAT_TTL_SEC

    # --- hardware (psutil) -------------------------------------------------
    # cpu_percent(interval=None) is non-blocking; the first call after import
    # returns 0.0, which is corrected on the next loop iteration.
    cpu_pct = float(psutil.cpu_percent(interval=None))
    vm = psutil.virtual_memory()
    ram_used_gb = (vm.total - vm.available) / 1e9
    ram_total_gb = vm.total / 1e9
    try:
        nvme_free_gb = shutil.disk_usage(NVME_PATH).free / 1e9
    except Exception:
        nvme_free_gb = 0.0

    hw: dict[str, float | None] = {
        "system:cpu_pct": round(cpu_pct, 1),
        "system:ram_used_gb": round(ram_used_gb, 2),
        "system:ram_total_gb": round(ram_total_gb, 2),
        "system:nvme_free_gb": round(nvme_free_gb, 1),
        "system:temp_c": _read_cpu_temp(),
        "system:npu_pct": _read_npu_util(),
    }

    pipe = redis.pipeline()
    for key, val in hw.items():
        if val is None:
            # Clear stale value so the gauge disappears rather than freezing.
            pipe.delete(key)
        else:
            pipe.setex(key, ttl, val)

    # --- model versions ----------------------------------------------------
    for short, ver in _model_versions().items():
        key = f"models:{short.replace('_version', '')}_version"
        if ver is None:
            pipe.delete(key)
        else:
            pipe.setex(key, ttl, ver)

    # --- heartbeats for in-process / probed services ----------------------
    # The backend owns the redis + postgres connections and the agent loop, so
    # their liveness is the backend's liveness (plus an actual probe for PG).
    pipe.setex("heartbeat:redis", ttl, ts)
    pipe.setex("heartbeat:agent", ttl, ts)
    await pipe.execute()

    if await _check_postgres():
        await redis.setex("heartbeat:postgres", ttl, ts)


async def metrics_loop(redis: Any, interval: int = METRICS_INTERVAL_SEC) -> None:
    """Forever-loop wrapper. Tolerant of transient redis/psutil hiccups."""
    logger.info(
        "system-metrics producer started (interval=%ss, ttl=%ss)", interval, HEARTBEAT_TTL_SEC
    )
    while True:
        try:
            await collect_once(redis)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("system-metrics sample failed: %s", exc)
        await asyncio.sleep(interval)

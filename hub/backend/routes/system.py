"""System health metrics endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/system", tags=["system"])


def _f(s: str | None) -> float | None:
    return float(s) if s else None


def _i(s: str | None) -> int | None:
    return int(float(s)) if s else None


@router.get("/health")
async def system_health(request: Request) -> dict[str, Any]:
    """Return hardware metrics, service heartbeats, model versions from Redis."""
    redis = request.app.state.redis

    keys = [
        "system:cpu_pct",
        "system:ram_used_gb",
        "system:ram_total_gb",
        "system:nvme_free_gb",
        "system:npu_pct",
        "system:temp_c",
        "models:cv_version",
        "models:llm_version",
        "models:whisper_version",
        "metrics:cv_p50_ms",
        "metrics:cv_p95_ms",
        "metrics:voice_e2e_p50_ms",
        "sync:last_bridge_ts",
    ]
    values = await redis.mget(*keys)
    (
        cpu_pct,
        ram_used,
        ram_total,
        nvme_free,
        npu_pct,
        temp_c,
        cv_ver,
        llm_ver,
        whisper_ver,
        cv_p50,
        cv_p95,
        voice_p50,
        last_bridge,
    ) = values

    # Service heartbeats (edge processes set these with TTL 60s)
    svc_names = ["cv", "voice", "agent", "mqtt", "postgres", "redis"]
    hb_keys = [f"heartbeat:{s}" for s in svc_names]
    heartbeats = await redis.mget(*hb_keys)
    services = [
        {
            "name": name,
            "status": "ok" if hb else "offline",
            "uptime": hb,
        }
        for name, hb in zip(svc_names, heartbeats, strict=True)
    ]

    t1_queue_depth = await redis.llen("sync:t1_queue")

    return {
        "services": services,
        "hardware": {
            "cpu_pct": _f(cpu_pct) or 0.0,
            "ram_used_gb": _f(ram_used) or 0.0,
            "ram_total_gb": _f(ram_total) or 8.0,
            "nvme_free_gb": _f(nvme_free) or 0.0,
            "npu_pct": _f(npu_pct),
            "temp_c": _f(temp_c),
        },
        "latency": {
            "cv_p50_ms": _i(cv_p50),
            "cv_p95_ms": _i(cv_p95),
            "voice_e2e_p50_ms": _i(voice_p50),
        },
        "models": {
            "cv_version": cv_ver,
            "llm_version": llm_ver,
            "whisper_version": whisper_ver,
        },
        "sync": {
            "last_bridge_ts": last_bridge,
            "t1_queue_depth": t1_queue_depth or 0,
        },
    }

"""Unit tests for hub.backend.services.system_metrics (the System dashboard producer)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hub.backend.services import system_metrics


def _make_redis() -> tuple[AsyncMock, MagicMock]:
    """Return (redis, pipe) where redis.pipeline() yields a call-recording pipe."""
    pipe = MagicMock()
    pipe.setex = MagicMock(return_value=None)
    pipe.delete = MagicMock(return_value=None)
    pipe.execute = AsyncMock(return_value=None)

    redis = AsyncMock()
    redis.pipeline = MagicMock(return_value=pipe)
    redis.setex = AsyncMock(return_value=None)
    return redis, pipe


@pytest.mark.asyncio
async def test_collect_once_writes_hardware_and_heartbeats() -> None:
    redis, pipe = _make_redis()
    with (
        patch.object(system_metrics, "_check_postgres", AsyncMock(return_value=True)),
        patch.object(system_metrics, "_model_versions", return_value={"cv_version": None}),
        patch.object(system_metrics, "_read_npu_util", return_value=None),
        patch.object(system_metrics, "_read_cpu_temp", return_value=47.0),
    ):
        await system_metrics.collect_once(redis)

    setex_keys = {call.args[0] for call in pipe.setex.call_args_list}
    # Hardware gauges that always have a value.
    assert "system:cpu_pct" in setex_keys
    assert "system:ram_total_gb" in setex_keys
    assert "system:nvme_free_gb" in setex_keys
    assert "system:temp_c" in setex_keys  # 47.0 — present
    # In-process service heartbeats.
    assert "heartbeat:redis" in setex_keys
    assert "heartbeat:agent" in setex_keys
    # Probed postgres heartbeat is written on the redis client directly.
    redis.setex.assert_awaited_once()
    assert redis.setex.await_args.args[0] == "heartbeat:postgres"


@pytest.mark.asyncio
async def test_collect_once_clears_unavailable_metrics() -> None:
    """None-valued metrics (no NPU, no temp) are DELETEd so gauges disappear."""
    redis, pipe = _make_redis()
    with (
        patch.object(system_metrics, "_check_postgres", AsyncMock(return_value=False)),
        patch.object(system_metrics, "_model_versions", return_value={"cv_version": None}),
        patch.object(system_metrics, "_read_npu_util", return_value=None),
        patch.object(system_metrics, "_read_cpu_temp", return_value=None),
    ):
        await system_metrics.collect_once(redis)

    delete_keys = {call.args[0] for call in pipe.delete.call_args_list}
    assert "system:npu_pct" in delete_keys
    assert "system:temp_c" in delete_keys
    assert "models:cv_version" in delete_keys
    # Postgres probe failed → no heartbeat:postgres written.
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_once_writes_model_versions() -> None:
    redis, pipe = _make_redis()
    with (
        patch.object(system_metrics, "_check_postgres", AsyncMock(return_value=True)),
        patch.object(
            system_metrics,
            "_model_versions",
            return_value={"cv_version": "yolo26n_v3", "whisper_version": "moonshine-uk"},
        ),
        patch.object(system_metrics, "_read_npu_util", return_value=None),
        patch.object(system_metrics, "_read_cpu_temp", return_value=None),
    ):
        await system_metrics.collect_once(redis)

    setex = {call.args[0]: call.args[2] for call in pipe.setex.call_args_list}
    assert setex["models:cv_version"] == "yolo26n_v3"
    assert setex["models:whisper_version"] == "moonshine-uk"

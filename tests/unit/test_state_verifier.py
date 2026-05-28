"""Unit tests for hub.edge.agent.state_verifier."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hub.edge.agent.state_verifier import StateVerifier, VerificationResult


def _make_redis(states: list[dict[str, Any]]) -> AsyncMock:
    """Return a Redis mock whose hgetall returns *states* in sequence (last repeated)."""
    redis = MagicMock()
    call_count = 0

    async def hgetall(key: str) -> dict[str, Any]:
        nonlocal call_count
        idx = min(call_count, len(states) - 1)
        call_count += 1
        return states[idx]

    redis.hgetall = hgetall
    return redis


# ── CONFIRMED ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirmed_immediately() -> None:
    """State already matches expected on first poll."""
    redis = _make_redis([{"state": "ON"}])
    verifier = StateVerifier(redis, timeout_sec=1.0)
    result = await verifier.expect_change("dev1", {"state": "ON"})
    assert result == VerificationResult.CONFIRMED


@pytest.mark.asyncio
async def test_confirmed_after_delay() -> None:
    """State matches on second poll (device responds slightly after command)."""
    redis = _make_redis([{}, {}, {"state": "on"}])
    verifier = StateVerifier(redis, timeout_sec=2.0)
    result = await verifier.expect_change("dev1", {"state": "on"})
    assert result == VerificationResult.CONFIRMED


@pytest.mark.asyncio
async def test_confirmed_case_insensitive() -> None:
    """Comparison is case-insensitive: payload "ON" matches Redis "on"."""
    redis = _make_redis([{"state": "on"}])
    verifier = StateVerifier(redis, timeout_sec=1.0)
    result = await verifier.expect_change("dev1", {"state": "ON"})
    assert result == VerificationResult.CONFIRMED


@pytest.mark.asyncio
async def test_confirmed_subset_match() -> None:
    """Extra fields in current state are ignored; only expected keys checked."""
    redis = _make_redis([{"state": "on", "brightness": "80", "color_temp": "4000"}])
    verifier = StateVerifier(redis, timeout_sec=1.0)
    result = await verifier.expect_change("dev1", {"state": "on"})
    assert result == VerificationResult.CONFIRMED


@pytest.mark.asyncio
async def test_confirmed_bytes_keys_and_values() -> None:
    """Redis may return bytes; verifier should decode them transparently."""
    redis = _make_redis([{b"state": b"ON"}])
    verifier = StateVerifier(redis, timeout_sec=1.0)
    result = await verifier.expect_change("dev1", {"state": "on"})
    assert result == VerificationResult.CONFIRMED


# ── STATE_NOT_TRACKED ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_not_tracked_empty_before_and_after() -> None:
    """Hash is empty before and stays empty → device doesn't report state."""
    redis = _make_redis([{}])
    verifier = StateVerifier(redis, timeout_sec=0.05)
    result = await verifier.expect_change("dev_no_state", {"state": "on"}, before={})
    assert result == VerificationResult.STATE_NOT_TRACKED


# ── TIMEOUT ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_hash_was_populated_before() -> None:
    """Hash had prior state, new expected state never arrived — TIMEOUT."""
    redis = _make_redis([{"state": "off"}])
    verifier = StateVerifier(redis, timeout_sec=0.05)
    before = {"state": "off"}
    result = await verifier.expect_change("dev1", {"state": "on"}, before=before)
    assert result == VerificationResult.TIMEOUT


@pytest.mark.asyncio
async def test_timeout_hash_empty_but_before_was_set() -> None:
    """Hash became empty after command, before-state was non-empty — TIMEOUT."""
    redis = _make_redis([{}])
    verifier = StateVerifier(redis, timeout_sec=0.05)
    result = await verifier.expect_change("dev1", {"state": "on"}, before={"state": "off"})
    assert result == VerificationResult.TIMEOUT


# ── MISMATCH ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mismatch_wrong_value() -> None:
    """State appeared in hash but with wrong value."""
    redis = _make_redis([{"state": "off"}])
    verifier = StateVerifier(redis, timeout_sec=0.05)
    result = await verifier.expect_change("dev1", {"state": "on"}, before={})
    assert result == VerificationResult.MISMATCH


@pytest.mark.asyncio
async def test_mismatch_partial_match() -> None:
    """Only some expected keys match — not a full subset match → MISMATCH."""
    redis = _make_redis([{"state": "on", "brightness": "10"}])
    verifier = StateVerifier(redis, timeout_sec=0.05)
    result = await verifier.expect_change("dev1", {"state": "on", "brightness": "80"}, before={})
    assert result == VerificationResult.MISMATCH

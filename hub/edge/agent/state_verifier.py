"""Verify that a device's state changed after a command.

Reads Redis ``home:state:{device_id}`` hash.  The hash is populated by
``mqtt_subscriber`` when it receives a message on the device's configured
state topic (subscribed via the ``home/+/+/state`` wildcard).
"""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SEC = 0.2


class VerificationResult(StrEnum):
    CONFIRMED = "confirmed"
    TIMEOUT = "timeout"
    MISMATCH = "mismatch"
    STATE_NOT_TRACKED = "not_tracked"


class StateVerifier:
    def __init__(self, redis_client: Any, timeout_sec: float = 3.0) -> None:
        self._redis = redis_client
        self._timeout_sec = timeout_sec

    async def expect_change(
        self,
        device_id: str,
        expected: dict[str, Any],
        before: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """Poll ``home:state:{device_id}`` until expected state is seen or timeout.

        Args:
            device_id: Device identifier used as the Redis hash key suffix.
            expected: Subset of state fields that must match (case-insensitive),
                e.g. ``{"state": "ON"}``.
            before: Pre-command snapshot; if *None*, read from Redis now.

        Returns:
            * ``CONFIRMED`` — state matched within *timeout_sec*.
            * ``TIMEOUT`` — hash is tracked but state never matched.
            * ``MISMATCH`` — hash has data but values don't match.
            * ``STATE_NOT_TRACKED`` — hash was empty before and after command.
        """
        key = f"home:state:{device_id}"

        if before is None:
            raw_before: dict[Any, Any] = await self._redis.hgetall(key)
            before = _decode(raw_before)

        expected_norm = _normalize(expected)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._timeout_sec

        while loop.time() < deadline:
            raw: dict[Any, Any] = await self._redis.hgetall(key)
            current = _decode(raw)
            if current and _matches(current, expected_norm):
                return VerificationResult.CONFIRMED
            await asyncio.sleep(_POLL_INTERVAL_SEC)

        # Timed out — determine the failure mode
        raw_final: dict[Any, Any] = await self._redis.hgetall(key)
        final = _decode(raw_final)

        if not final and not before:
            return VerificationResult.STATE_NOT_TRACKED
        if _matches(final, expected_norm):
            return VerificationResult.CONFIRMED
        if not final or final == before:
            # Hash is empty (was tracked) or state hasn't changed — device silent
            return VerificationResult.TIMEOUT
        # State changed but to a different value
        return VerificationResult.MISMATCH


def _decode(raw: dict[Any, Any]) -> dict[str, str]:
    """Normalize Redis bytes→str in returned hash."""
    result: dict[str, str] = {}
    for k, v in raw.items():
        str_k = k.decode() if isinstance(k, bytes) else str(k)
        str_v = v.decode() if isinstance(v, bytes) else str(v)
        result[str_k] = str_v
    return result


def _normalize(d: dict[str, Any]) -> dict[str, str]:
    """Lower-case all values for case-insensitive comparison."""
    return {str(k): str(v).lower() for k, v in d.items()}


def _matches(current: dict[str, str], expected_norm: dict[str, str]) -> bool:
    """True if every expected key=value exists in *current* (case-insensitive)."""
    current_norm = {k: v.lower() for k, v in current.items()}
    return all(current_norm.get(k) == v for k, v in expected_norm.items())

"""ntfy publishing helper for edge (local LAN) and cloud push notifications."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def publish(
    base_url: str,
    topic: str,
    message: str,
    title: str | None = None,
    priority: str = "default",
    tags: list[str] | None = None,
    click_url: str | None = None,
) -> bool:
    """Publish to ntfy topic. Returns True on success."""
    headers: dict[str, str] = {"Priority": priority}
    if title:
        headers["Title"] = title
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{base_url}/{topic}",
                content=message,
                headers=headers,
            )
            resp.raise_for_status()
            return True
    except httpx.HTTPError:
        logger.warning("ntfy publish failed to %s/%s", base_url, topic)
        return False


async def alert(
    base_url: str,
    room: str,
    event_type: str,
    confidence: float,
) -> bool:
    """Convenience wrapper for CV alert notifications."""
    return await publish(
        base_url=base_url,
        topic="iot-alerts",
        message=f"{event_type} detected in {room} (confidence: {confidence:.0%})",
        title=f"IoT Hub Alert — {room}",
        priority="high" if confidence > 0.8 else "default",
        tags=["warning", event_type],
    )

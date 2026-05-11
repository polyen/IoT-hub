"""Unit tests for hub.edge.sync.ntfy_local."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from hub.edge.sync.ntfy_local import alert, publish


@pytest.mark.asyncio
async def test_publish_returns_true_on_200() -> None:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("hub.edge.sync.ntfy_local.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await publish(
            base_url="http://ntfy.local",
            topic="test",
            message="hello",
        )

    assert result is True


@pytest.mark.asyncio
async def test_publish_returns_false_on_connection_error() -> None:
    with patch("hub.edge.sync.ntfy_local.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        result = await publish(
            base_url="http://ntfy.local",
            topic="test",
            message="hello",
        )

    assert result is False


@pytest.mark.asyncio
async def test_alert_sets_high_priority_when_confidence_above_threshold() -> None:
    captured_headers: dict[str, str] = {}

    async def fake_post(url: str, content: str, headers: dict[str, str]) -> MagicMock:
        captured_headers.update(headers)
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp

    with patch("hub.edge.sync.ntfy_local.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = fake_post
        mock_client_cls.return_value = mock_client

        result = await alert(
            base_url="http://ntfy.local",
            room="living_room",
            event_type="fall",
            confidence=0.95,
        )

    assert result is True
    assert captured_headers["Priority"] == "high"

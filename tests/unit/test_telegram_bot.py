"""Unit tests for hub.cloud.telegram_bot."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build fake Telegram update / callback query objects
# ---------------------------------------------------------------------------


def _make_callback_query(data: str) -> MagicMock:
    """Build a minimal fake CallbackQuery with the given callback_data."""
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    return query


def _make_update(callback_query: MagicMock) -> MagicMock:
    update = MagicMock()
    update.callback_query = callback_query
    return update


def _make_context() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Tests: callback_data parsing → correct label forwarded to edge API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_query_handler_tp() -> None:
    """fb:{uuid}:tp → user_label='tp' posted to edge API."""
    from hub.cloud.telegram_bot import AlertBot

    alert_id = str(uuid.uuid4())
    bot = AlertBot(token="TEST_TOKEN", edge_api_url="http://edge.local:8000")

    query = _make_callback_query(f"fb:{alert_id}:tp")
    update = _make_update(query)
    context = _make_context()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("hub.cloud.telegram_bot.httpx.AsyncClient", return_value=mock_client):
        await bot.callback_query_handler(update, context)

    mock_client.post.assert_awaited_once_with(
        "http://edge.local:8000/api/feedback",
        json={
            "alert_id": alert_id,
            "user_label": "tp",
            "tag": None,
            "source": "telegram",
        },
    )
    query.answer.assert_awaited_once()
    # Confirm ✓ appears in the answer
    answer_text: str = query.answer.call_args[0][0]
    assert "✓" in answer_text or "Recorded" in answer_text


@pytest.mark.asyncio
async def test_callback_query_handler_fp() -> None:
    """fb:{uuid}:fp → user_label='fp' posted to edge API."""
    from hub.cloud.telegram_bot import AlertBot

    alert_id = str(uuid.uuid4())
    bot = AlertBot(token="TEST_TOKEN", edge_api_url="http://edge.local:8000")

    query = _make_callback_query(f"fb:{alert_id}:fp")
    update = _make_update(query)
    context = _make_context()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("hub.cloud.telegram_bot.httpx.AsyncClient", return_value=mock_client):
        await bot.callback_query_handler(update, context)

    mock_client.post.assert_awaited_once_with(
        "http://edge.local:8000/api/feedback",
        json={
            "alert_id": alert_id,
            "user_label": "fp",
            "tag": None,
            "source": "telegram",
        },
    )


@pytest.mark.asyncio
async def test_callback_query_handler_not_sure() -> None:
    """fb:{uuid}:not_sure → user_label='not_sure' posted to edge API."""
    from hub.cloud.telegram_bot import AlertBot

    alert_id = str(uuid.uuid4())
    bot = AlertBot(token="TEST_TOKEN", edge_api_url="http://edge.local:8000")

    query = _make_callback_query(f"fb:{alert_id}:not_sure")
    update = _make_update(query)
    context = _make_context()

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("hub.cloud.telegram_bot.httpx.AsyncClient", return_value=mock_client):
        await bot.callback_query_handler(update, context)

    mock_client.post.assert_awaited_once_with(
        "http://edge.local:8000/api/feedback",
        json={
            "alert_id": alert_id,
            "user_label": "not_sure",
            "tag": None,
            "source": "telegram",
        },
    )


@pytest.mark.asyncio
async def test_callback_query_unknown_format() -> None:
    """Malformed callback_data → answer called with 'Unknown action', no HTTP call."""
    from hub.cloud.telegram_bot import AlertBot

    bot = AlertBot(token="TEST_TOKEN", edge_api_url="http://edge.local:8000")

    query = _make_callback_query("malformed_data")
    update = _make_update(query)
    context = _make_context()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("hub.cloud.telegram_bot.httpx.AsyncClient", return_value=mock_client):
        await bot.callback_query_handler(update, context)

    mock_client.post.assert_not_called()
    query.answer.assert_awaited_once_with("Unknown action")


# ---------------------------------------------------------------------------
# Test: handle_alert sends message with correct text + keyboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_alert_sends_message() -> None:
    """handle_alert should call bot.send_message for each registered chat_id."""
    from hub.cloud.telegram_bot import AlertBot

    alert_id = str(uuid.uuid4())
    bot = AlertBot(token="TEST_TOKEN", edge_api_url="http://edge.local:8000")
    bot.add_chat_id(12345)

    # Telegram Bot is a frozen object; mock application.bot entirely
    mock_tg_bot = MagicMock()
    mock_send = AsyncMock()
    mock_tg_bot.send_message = mock_send
    bot.application = MagicMock()  # type: ignore[assignment]
    bot.application.bot = mock_tg_bot

    await bot.handle_alert(
        alert_id=alert_id,
        room="kitchen",
        type="fire",
        confidence=0.92,
        model_version="yolov8n-v3",
    )

    mock_send.assert_awaited_once()
    call_kwargs: dict[str, Any] = mock_send.call_args.kwargs
    assert call_kwargs["chat_id"] == 12345
    assert "fire" in call_kwargs["text"]
    assert "kitchen" in call_kwargs["text"]
    assert "92%" in call_kwargs["text"]
    assert "yolov8n-v3" in call_kwargs["text"]
    # Keyboard should contain 3 buttons
    markup = call_kwargs["reply_markup"]
    buttons = markup.inline_keyboard[0]
    assert len(buttons) == 3
    cb_data_values = [b.callback_data for b in buttons]
    assert any("tp" in d for d in cb_data_values)
    assert any("fp" in d for d in cb_data_values)
    assert any("not_sure" in d for d in cb_data_values)


@pytest.mark.asyncio
async def test_handle_alert_no_chat_ids() -> None:
    """handle_alert with no registered chat_ids should not raise and not call send_message."""
    from hub.cloud.telegram_bot import AlertBot

    bot = AlertBot(token="TEST_TOKEN", edge_api_url="http://edge.local:8000")

    mock_tg_bot = MagicMock()
    mock_send = AsyncMock()
    mock_tg_bot.send_message = mock_send
    bot.application = MagicMock()  # type: ignore[assignment]
    bot.application.bot = mock_tg_bot

    await bot.handle_alert(
        alert_id=str(uuid.uuid4()),
        room="garage",
        type="smoke",
        confidence=0.5,
        model_version="yolov8n-v2",
    )

    mock_send.assert_not_called()

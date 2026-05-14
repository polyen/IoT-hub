"""Telegram bot for HITL feedback on CV alerts.

Webhook-based async bot running on VPS. On alert fires (via MQTT), sends
a message with TP/FP/Not Sure inline buttons. Clicking calls back and POSTs
to the edge /api/feedback endpoint (including frame_blob_ref so the mining
loop can copy the corresponding T0 frame into the training set).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import OrderedDict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from pydantic_settings import BaseSettings, SettingsConfigDict
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_token: str = ""
    telegram_webhook_url: str = ""
    # Comma-separated list of pre-approved chat IDs (e.g. "123456789,987654321")
    telegram_allowed_chat_ids: str = ""
    edge_api_url: str = "http://backend:8000"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 8883
    database_url: str = "postgresql+asyncpg://iothub:iothub@localhost:5432/iothub"
    log_level: str = "info"


settings = BotSettings()


# ---------------------------------------------------------------------------
# AlertBot
# ---------------------------------------------------------------------------


_MAX_FRAME_CACHE = 512  # bounded in-memory cache: alert_id → frame_blob_ref


class AlertBot:
    """Webhook-based Telegram bot for HITL CV alert feedback."""

    def __init__(
        self,
        token: str,
        edge_api_url: str,
        allowed_chat_ids: list[int] | None = None,
        db_session_factory: Any = None,
    ) -> None:
        self.token = token
        self.edge_api_url = edge_api_url
        self.db_session_factory = db_session_factory
        self._chat_ids: set[int] = set(allowed_chat_ids or [])
        # alert_id → frame_blob_ref (capped to avoid unbounded growth)
        self._frame_refs: OrderedDict[str, str | None] = OrderedDict()

        self.application = Application.builder().token(token).build()
        self.application.add_handler(CommandHandler("start", self.start_handler))
        self.application.add_handler(CallbackQueryHandler(self.callback_query_handler))

    async def set_webhook(self, url: str) -> None:
        """Register webhook URL with Telegram."""
        await self.application.bot.set_webhook(url=url)
        logger.info("Webhook set to %s", url)

    def add_chat_id(self, chat_id: int) -> None:
        """Register a chat ID to receive alert notifications."""
        self._chat_ids.add(chat_id)

    def _cache_frame_ref(self, alert_id: str, frame_blob_ref: str | None) -> None:
        """Store frame_blob_ref for alert_id, evicting oldest entries when full."""
        if alert_id in self._frame_refs:
            self._frame_refs.move_to_end(alert_id)
        else:
            if len(self._frame_refs) >= _MAX_FRAME_CACHE:
                self._frame_refs.popitem(last=False)
            self._frame_refs[alert_id] = frame_blob_ref

    async def start_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Register the chat on /start and confirm subscription to alerts."""
        if update.effective_chat is None:
            return
        chat_id = update.effective_chat.id
        self.add_chat_id(chat_id)
        logger.info("/start from chat_id=%s — subscribed to alerts", chat_id)
        await update.message.reply_text(  # type: ignore[union-attr]
            "✅ Підписано на сповіщення IoT Hub.\n"
            "Ви будете отримувати кнопки TP / FP / Not Sure для кожного CV-алерту."
        )

    async def handle_alert(
        self,
        alert_id: str,
        room: str,
        alert_type: str,
        confidence: float,
        model_version: str,
        frame_blob_ref: str | None = None,
    ) -> None:
        """Send alert notification with TP/FP/Not Sure inline keyboard."""
        self._cache_frame_ref(alert_id, frame_blob_ref)

        text = (
            f"\U0001f514 Alert: {alert_type} in {room}\n"
            f"Confidence: {confidence:.0%}\n"
            f"Model: {model_version}"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ TP", callback_data=f"fb:{alert_id}:tp"),
                    InlineKeyboardButton("❌ FP", callback_data=f"fb:{alert_id}:fp"),
                    InlineKeyboardButton("🤷 Not sure", callback_data=f"fb:{alert_id}:not_sure"),
                ]
            ]
        )
        for chat_id in list(self._chat_ids):
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                )
            except Exception:
                logger.exception("Failed to send alert to chat_id=%s", chat_id)

    async def callback_query_handler(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle inline button presses — forward feedback to edge API."""
        query = update.callback_query
        if query is None or query.data is None:
            return

        parts = query.data.split(":")
        if len(parts) != 3 or parts[0] != "fb":
            logger.warning("Unexpected callback_data: %s", query.data)
            await query.answer("Unknown action")
            return

        _, alert_id, label = parts
        # Include frame_blob_ref so the mining loop can copy the T0 frame
        frame_blob_ref = self._frame_refs.get(alert_id)
        payload: dict[str, Any] = {
            "alert_id": alert_id,
            "user_label": label,
            "tag": None,
            "source": "telegram",
        }
        if frame_blob_ref is not None:
            payload["frame_blob_ref"] = frame_blob_ref

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self.edge_api_url}/api/feedback",
                    json=payload,
                )
                resp.raise_for_status()
            logger.info(
                "Feedback posted: alert_id=%s label=%s frame_blob_ref=%s",
                alert_id,
                label,
                frame_blob_ref,
            )
        except httpx.HTTPError:
            logger.exception("Failed to post feedback to edge API")

        label_display = {"tp": "✅ TP", "fp": "❌ FP", "not_sure": "🤷 Not sure"}.get(label, label)
        await query.answer(f"Зафіксовано: {label_display}")

    async def process_update(self, data: dict[str, Any]) -> None:
        """Feed a raw Telegram update dict into the application."""
        update = Update.de_json(data, self.application.bot)
        await self.application.process_update(update)


# ---------------------------------------------------------------------------
# MQTT subscription helper
# ---------------------------------------------------------------------------


async def _mqtt_listener(bot: AlertBot) -> None:
    """Subscribe to home/+/alert on the VPS MQTT broker and call handle_alert."""
    try:
        import aiomqtt
    except ImportError:
        logger.error("aiomqtt not installed — MQTT subscription disabled")
        return

    topic = "home/+/alert"
    async with aiomqtt.Client(
        hostname=settings.mqtt_host,
        port=settings.mqtt_port,
    ) as client:
        await client.subscribe(topic)
        logger.info(
            "Subscribed to MQTT topic %s on %s:%s", topic, settings.mqtt_host, settings.mqtt_port
        )
        async for message in client.messages:
            try:
                payload = json.loads(message.payload)
                await bot.handle_alert(
                    alert_id=str(payload.get("alert_id", "")),
                    room=str(payload.get("room", "unknown")),
                    alert_type=str(payload.get("type", "unknown")),
                    confidence=float(payload.get("confidence", 0.0)),
                    model_version=str(payload.get("model_version", "unknown")),
                    frame_blob_ref=payload.get("frame_blob_ref") or None,
                )
            except (json.JSONDecodeError, KeyError):
                logger.exception("Malformed MQTT alert payload: %s", message.payload)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

_bot: AlertBot | None = None
_mqtt_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _bot, _mqtt_task

    allowed_ids = [
        int(cid.strip())
        for cid in settings.telegram_allowed_chat_ids.split(",")
        if cid.strip().lstrip("-").isdigit()
    ]
    _bot = AlertBot(
        token=settings.telegram_token,
        edge_api_url=settings.edge_api_url,
        allowed_chat_ids=allowed_ids or None,
    )
    await _bot.application.initialize()

    if settings.telegram_webhook_url:
        await _bot.set_webhook(settings.telegram_webhook_url)

    # Start MQTT listener in background
    if settings.mqtt_host:
        _mqtt_task = asyncio.create_task(_mqtt_listener(_bot))

    yield

    if _mqtt_task is not None:
        _mqtt_task.cancel()
        try:
            await _mqtt_task
        except asyncio.CancelledError:
            pass

    if _bot is not None:
        await _bot.application.shutdown()


app = FastAPI(title="IoT Hub Telegram Bot", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request) -> Response:
    """Receive Telegram webhook updates."""
    global _bot
    if _bot is None or token != settings.telegram_token:
        return Response(status_code=403)

    data = await request.json()
    await _bot.process_update(data)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        stream=sys.stdout,
    )
    uvicorn.run(
        "hub.cloud.telegram_bot:app",
        host="0.0.0.0",
        port=8001,
        log_level=settings.log_level.lower(),
    )

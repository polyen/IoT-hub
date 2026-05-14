from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class DeviceStateOut(BaseModel):
    device_id: str
    state: dict[str, Any]


class CommandBody(BaseModel):
    payload: dict[str, Any]
    intent_text: str = "UI command"


class CommandResult(BaseModel):
    result: str  # "auto_executed" | "confirm_required" | "denied"
    confirm_id: str | None = None

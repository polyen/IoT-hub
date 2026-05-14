from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: str
    intent_text: str
    tool: str | None
    action_class: str
    executed: bool
    confirmation: str | None
    latency_ms: int | None
    llm_version: str | None


class TryBody(BaseModel):
    intent_text: str
    tool: str | None = None
    payload: dict[str, Any] | None = None


class TryResult(BaseModel):
    matched_rule: str
    action_class: str
    reason: str
    latency_ms: int

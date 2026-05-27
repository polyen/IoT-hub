from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    timestamp: datetime
    room: str | None
    type: str
    tier: int
    payload: dict[str, Any] | None
    model_version: str | None
    user_consent_cloud: bool
    # Latest user feedback label for this event (tp/fp/not_sure), or None.
    user_feedback: str | None = None

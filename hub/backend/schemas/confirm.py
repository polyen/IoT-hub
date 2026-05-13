from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class ConfirmRequestOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    tool: str
    payload: dict[str, Any]
    intent_text: str
    confirm_message: str
    schedule_origin: str | None
    state: str
    decided_by: str | None
    decided_at: datetime | None

    model_config = {"from_attributes": True}


class DecideBody(BaseModel):
    decision: Literal["approve", "reject"]

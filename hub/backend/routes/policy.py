"""Policy read-only endpoints: view, lint, simulate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from hub.backend.services.policy_loader import lint_policy, load_policy, simulate

router = APIRouter(prefix="/api/policy", tags=["policy"])


class SimulateBody(BaseModel):
    intent_text: str
    tool: str | None = None
    payload: dict[str, Any] | None = None


@router.get("")
async def get_policy() -> dict[str, Any]:
    return load_policy()


@router.get("/lint")
async def get_lint() -> list[dict[str, str]]:
    return lint_policy()


@router.post("/simulate")
async def simulate_policy(body: SimulateBody) -> dict[str, Any]:
    return simulate(body.intent_text, body.tool, body.payload)

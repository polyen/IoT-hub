"""Model deployment API — atomic HEF promote / rollback endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from hub.backend.config import settings
from hub.edge.mlops.deploy import ModelStore

router = APIRouter(prefix="/api/deploy", tags=["deploy"])


async def verify_token(
    x_deploy_token: Annotated[str, Header(alias="X-Deploy-Token")],
) -> None:
    """Validate the deploy token supplied in the X-Deploy-Token header."""
    if not settings.deploy_token or x_deploy_token != settings.deploy_token:
        raise HTTPException(status_code=401, detail="Invalid or missing deploy token")


TokenDep = Annotated[None, Depends(verify_token)]


@router.post("/promote/{version}")
async def promote(version: str, _auth: TokenDep) -> dict[str, Any]:
    """Atomically promote *version* to the active model and SIGHUP the CV container."""
    store = ModelStore()
    try:
        store.promote(version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "promoted", "version": version}


@router.post("/rollback")
async def rollback(_auth: TokenDep) -> dict[str, Any]:
    """Roll back to the previous model version."""
    store = ModelStore()
    prev = store.rollback()
    if prev is None:
        raise HTTPException(status_code=409, detail="No prior model version available for rollback")
    return {"status": "rolled_back", "version": prev}

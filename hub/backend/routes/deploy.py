"""Model deployment API — atomic HEF promote / rollback endpoints.

Endpoints:
    POST /api/deploy/promote/{version}                 (yolo, back-compat)
    POST /api/deploy/{kind}/promote/{version}          (yolo|pose|face|whisper)
    POST /api/deploy/rollback                          (yolo, back-compat)
    POST /api/deploy/{kind}/rollback
    GET  /api/deploy/{kind}/status                     (current + history tail)

All write endpoints require ``X-Deploy-Token`` matching ``settings.deploy_token``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from hub.backend.config import settings
from hub.edge.mlops.deploy import (
    KNOWN_KINDS,
    ChecksumMismatchError,
    ModelStore,
)

router = APIRouter(prefix="/api/deploy", tags=["deploy"])


async def verify_token(
    x_deploy_token: Annotated[str, Header(alias="X-Deploy-Token")],
) -> None:
    """Validate the deploy token supplied in the X-Deploy-Token header."""
    if not settings.deploy_token or x_deploy_token != settings.deploy_token:
        raise HTTPException(status_code=401, detail="Invalid or missing deploy token")


TokenDep = Annotated[None, Depends(verify_token)]


def _store(kind: str) -> ModelStore:
    if kind not in KNOWN_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown kind {kind!r}. Allowed: {sorted(KNOWN_KINDS)}",
        )
    return ModelStore(kind=kind)


def _do_promote(kind: str, version: str) -> dict[str, Any]:
    store = _store(kind)
    try:
        store.promote(version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChecksumMismatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "promoted", "kind": kind, "version": version}


def _do_rollback(kind: str) -> dict[str, Any]:
    store = _store(kind)
    prev = store.rollback()
    if prev is None:
        raise HTTPException(status_code=409, detail="No prior model version available for rollback")
    return {"status": "rolled_back", "kind": kind, "version": prev}


# -- back-compat: implicit yolo kind ----------------------------------------


@router.post("/promote/{version}")
async def promote_yolo(version: str, _auth: TokenDep) -> dict[str, Any]:
    """Promote a YOLO model. Back-compat path (assumes kind=yolo)."""
    return _do_promote("yolo", version)


@router.post("/rollback")
async def rollback_yolo(_auth: TokenDep) -> dict[str, Any]:
    """Roll back the YOLO model. Back-compat path."""
    return _do_rollback("yolo")


# -- explicit kind ----------------------------------------------------------


@router.post("/{kind}/promote/{version}")
async def promote(kind: str, version: str, _auth: TokenDep) -> dict[str, Any]:
    """Atomically promote *version* of *kind* and SIGHUP its container."""
    return _do_promote(kind, version)


@router.post("/{kind}/rollback")
async def rollback(kind: str, _auth: TokenDep) -> dict[str, Any]:
    """Roll back *kind* to its previous version."""
    return _do_rollback(kind)


@router.get("/{kind}/status")
async def status(kind: str) -> dict[str, Any]:
    """Return current version + last 10 deployments for *kind* (read-only, no auth)."""
    store = _store(kind)
    history = [r.to_dict() for r in store._load_history() if r.kind == kind][-10:]
    return {
        "kind": kind,
        "current": store.current_version(),
        "available": store.list_versions(),
        "history": history,
    }

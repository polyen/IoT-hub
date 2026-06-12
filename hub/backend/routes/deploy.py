"""Model deployment API — atomic HEF promote / rollback / MLflow fetch endpoints.

Endpoints:
    POST /api/deploy/promote/{version}                 (yolo, back-compat)
    POST /api/deploy/{kind}/promote/{version}          (yolo|pose|face|whisper)
    POST /api/deploy/rollback                          (yolo, back-compat)
    POST /api/deploy/{kind}/rollback
    GET  /api/deploy/{kind}/status                     (current + history tail)
    POST /api/deploy/{kind}/fetch                      (pull HEF from MLflow)

All write endpoints require ``X-Deploy-Token`` matching ``settings.deploy_token``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from hub.backend.config import settings
from hub.edge.mlops.deploy import (
    KNOWN_KINDS,
    ChecksumMismatchError,
    ModelStore,
    SmokeTestError,
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
    except SmokeTestError as exc:
        raise HTTPException(status_code=422, detail=f"Smoke test failed: {exc}") from exc
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


@router.post("/{kind}/fetch")
async def fetch_from_mlflow(
    kind: str,
    body: dict[str, Any],
    _auth: TokenDep,
) -> dict[str, Any]:
    """Download a HEF artifact from MLflow and stage it for promotion.

    Body fields:
        run_id        (required) MLflow run ID
        artifact_path (required) path within the run artifacts, e.g. "model/best.hef"
        version       (optional) local version name; defaults to run_id[:8]
        mlflow_uri    (optional) tracking URI; defaults to MLFLOW_TRACKING_URI env or
                      http://localhost:5001

    On success the HEF is placed in versions/<version>.hef and manifest.json is
    updated with its SHA256. The version is NOT auto-promoted — call
    POST /api/deploy/{kind}/promote/{version} separately (after shadow testing).
    """
    try:
        import mlflow
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="mlflow not installed on this host") from exc

    run_id: str | None = body.get("run_id")
    artifact_path: str | None = body.get("artifact_path")
    if not run_id or not artifact_path:
        raise HTTPException(status_code=422, detail="run_id and artifact_path are required")

    mlflow_uri: str = body.get(
        "mlflow_uri",
        os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5001"),
    )
    version: str = body.get("version") or run_id[:8]

    store = _store(kind)
    versions_dir = store.models_dir / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    dest = versions_dir / f"{version}.hef"

    if dest.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Version {version!r} already exists at {dest}. Choose a different version name.",
        )

    try:
        mlflow.set_tracking_uri(mlflow_uri)
        local_path = mlflow.artifacts.download_artifacts(
            run_id=run_id,
            artifact_path=artifact_path,
            dst_path=str(versions_dir),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"MLflow download failed: {exc}") from exc

    downloaded = Path(local_path)
    if not downloaded.is_file() or downloaded.suffix != ".hef":
        raise HTTPException(
            status_code=422,
            detail=f"Downloaded artifact is not a .hef file: {downloaded}",
        )

    if downloaded != dest:
        downloaded.rename(dest)

    # Compute SHA256 and update manifest
    h = hashlib.sha256()
    with dest.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    sha256 = h.hexdigest()

    manifest_file = store.models_dir / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_file.is_file():
        try:
            manifest = json.loads(manifest_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    manifest[version] = {"sha256": sha256, "kind": kind, "size": dest.stat().st_size}
    tmp = manifest_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, manifest_file)

    return {
        "status": "fetched",
        "kind": kind,
        "version": version,
        "path": str(dest),
        "sha256": sha256,
        "size_bytes": dest.stat().st_size,
        "next_step": f"POST /api/deploy/{kind}/promote/{version}",
    }

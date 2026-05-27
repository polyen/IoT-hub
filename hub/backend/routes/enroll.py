"""Face enrollment endpoint — adds a live-captured embedding to embeddings.pkl."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["enroll"])

EMBEDDINGS_PATH = Path(os.environ.get("FACE_EMBEDDINGS_PATH", "/app/models/embeddings.pkl"))
CV_CONTAINER = os.environ.get("CV_CONTAINER", "cv")

# Minimum samples required for a stable ArcFace anchor. Single-frame enroll
# captures whatever the face looked like at the click moment (blur, eyes
# closed, profile view) and produces a poor enrollment; averaging across N
# frames smooths these out. The CV pipeline pushes one embedding per second
# per track, so 5 samples ≈ 5 s of in-frame presence.
MIN_ENROLL_SAMPLES = 5
ENROLL_WAIT_BUDGET_SEC = 8.0
ENROLL_POLL_INTERVAL_SEC = 0.5


class EnrollBody(BaseModel):
    room: str
    track_id: int
    name: str


class EnrollResponse(BaseModel):
    status: str
    enrolled_count: int


@router.get("/api/cv/enrollments")
async def list_enrollments() -> dict[str, Any]:
    """Return the names of all enrolled faces (without embeddings)."""
    enrolled: dict[str, list[float]] = {}
    if EMBEDDINGS_PATH.exists():
        try:
            with open(EMBEDDINGS_PATH, "rb") as f:
                enrolled = pickle.load(f)  # noqa: S301
        except (EOFError, pickle.UnpicklingError):
            pass
    return {"names": sorted(enrolled.keys()), "count": len(enrolled)}


@router.delete("/api/cv/enrollments/{name}")
async def delete_enrollment(name: str) -> dict[str, Any]:
    """Remove a named face from embeddings.pkl and hot-reload the CV pipeline."""
    if not EMBEDDINGS_PATH.exists():
        raise HTTPException(status_code=404, detail="No enrollments file found")

    enrolled: dict[str, list[float]] = {}
    try:
        with open(EMBEDDINGS_PATH, "rb") as f:
            enrolled = pickle.load(f)  # noqa: S301
    except (EOFError, pickle.UnpicklingError) as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt embeddings file: {exc}") from exc

    if name not in enrolled:
        raise HTTPException(status_code=404, detail=f"'{name}' not found in enrollments")

    del enrolled[name]

    tmp = EMBEDDINGS_PATH.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(enrolled, f)
    tmp.replace(EMBEDDINGS_PATH)

    logger.info("Deleted enrollment: '%s'", name)
    _sighup_cv()

    return {"status": "ok", "enrolled_count": len(enrolled)}


async def _collect_embeddings(redis: Any, redis_key: str) -> list[list[float]]:
    """Wait up to ENROLL_WAIT_BUDGET_SEC for at least MIN_ENROLL_SAMPLES embeddings.

    Returns the full list as soon as the minimum is met, or the longest list
    seen during the budget if the minimum is never reached (empty list means
    the track has no buffered embeddings at all — caller treats as 404).
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + ENROLL_WAIT_BUDGET_SEC
    raw_list: list[bytes] = []
    while True:
        raw_list = await redis.lrange(redis_key, 0, -1)
        if len(raw_list) >= MIN_ENROLL_SAMPLES:
            break
        if loop.time() >= deadline:
            break
        await asyncio.sleep(ENROLL_POLL_INTERVAL_SEC)

    embeddings: list[list[float]] = []
    for raw in raw_list:
        try:
            embeddings.append(json.loads(raw))
        except (ValueError, TypeError):
            continue
    return embeddings


@router.post("/api/cv/enroll", response_model=EnrollResponse)
async def enroll_face(body: EnrollBody, request: Request) -> EnrollResponse:
    """Name a face seen in the live camera feed.

    The CV pipeline pushes the per-track ArcFace embedding into a Redis list
    ``cv:face_embs:{room}:{track_id}`` (capped at MAX_ENROLL_EMBEDDINGS,
    90 s TTL). This endpoint waits briefly until at least MIN_ENROLL_SAMPLES
    are present, averages them, L2-normalizes, appends to ``embeddings.pkl``,
    and SIGHUPs the CV container to hot-reload.

    Status codes: 404 — no buffered embeddings (face left frame or TTL
    expired); 409 — track present but fewer than MIN_ENROLL_SAMPLES collected
    within the wait budget (person needs to stay in view longer).
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")

    redis = request.app.state.redis
    redis_key = f"cv:face_embs:{body.room}:{body.track_id}"
    embeddings = await _collect_embeddings(redis, redis_key)

    if not embeddings:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No embeddings buffered for track {body.track_id} in room "
                f"'{body.room}'. The face may have left the frame or the "
                "90-second window has expired — wait for the person to "
                "reappear and retry."
            ),
        )
    if len(embeddings) < MIN_ENROLL_SAMPLES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Only {len(embeddings)} sample(s) captured, need at least "
                f"{MIN_ENROLL_SAMPLES}. Keep the person facing the camera "
                "for a few more seconds and retry."
            ),
        )

    import numpy as np  # noqa: PLC0415

    arr = np.asarray(embeddings, dtype="float32")
    mean = arr.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    averaged: list[float] = (mean / norm if norm > 0 else mean).tolist()

    enrolled: dict[str, list[float]] = {}
    if EMBEDDINGS_PATH.exists():
        try:
            with open(EMBEDDINGS_PATH, "rb") as f:
                enrolled = pickle.load(f)  # noqa: S301 — local T0-derived file
        except (EOFError, pickle.UnpicklingError):
            logger.warning("embeddings.pkl corrupt — will overwrite")

    if name in enrolled:
        # Blend with existing anchor 50/50 (then re-normalize). Lets users
        # incrementally improve an enrollment by re-enrolling under
        # different lighting / head angles without erasing earlier samples.
        existing = np.asarray(enrolled[name], dtype="float32")
        blended = (existing + np.asarray(averaged, dtype="float32")) / 2.0
        blended_norm = float(np.linalg.norm(blended))
        enrolled[name] = (blended / blended_norm if blended_norm > 0 else blended).tolist()
        logger.info(
            "Updated enrollment for '%s' (blended with %d new samples)",
            name,
            len(embeddings),
        )
    else:
        enrolled[name] = averaged
        logger.info("New enrollment: '%s' from %d samples", name, len(embeddings))

    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EMBEDDINGS_PATH.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(enrolled, f)
    tmp.replace(EMBEDDINGS_PATH)

    _sighup_cv()

    return EnrollResponse(status="ok", enrolled_count=len(enrolled))


def _sighup_cv() -> None:
    try:
        result = subprocess.run(
            ["docker", "kill", "--signal=SIGHUP", CV_CONTAINER],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            logger.info("SIGHUP sent to %s — embeddings will reload", CV_CONTAINER)
        else:
            logger.warning(
                "docker kill SIGHUP %s failed (rc=%d): %s",
                CV_CONTAINER,
                result.returncode,
                result.stderr.decode(),
            )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("docker kill SIGHUP skipped: %s", exc)

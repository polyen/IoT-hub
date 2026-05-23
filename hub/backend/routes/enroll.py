"""Face enrollment endpoint — adds a live-captured embedding to embeddings.pkl."""

from __future__ import annotations

import json
import logging
import os
import pickle
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["enroll"])

EMBEDDINGS_PATH = Path(os.environ.get("FACE_EMBEDDINGS_PATH", "/app/models/embeddings.pkl"))
CV_CONTAINER = os.environ.get("CV_CONTAINER", "cv")


class EnrollBody(BaseModel):
    room: str
    track_id: int
    name: str


class EnrollResponse(BaseModel):
    status: str
    enrolled_count: int


@router.post("/api/cv/enroll", response_model=EnrollResponse)
async def enroll_face(body: EnrollBody, request: Request) -> EnrollResponse:
    """Name an unknown face seen in the live camera feed.

    The CV pipeline caches the ArcFace embedding for each person track in
    Redis under ``cv:face_emb:{room}:{track_id}`` with a 90-second TTL.
    This endpoint reads that embedding, appends it to ``embeddings.pkl``,
    and sends SIGHUP to the CV container so it hot-reloads without dropping
    the video stream.
    """
    redis = request.app.state.redis
    redis_key = f"cv:face_emb:{body.room}:{body.track_id}"
    raw: bytes | None = await redis.get(redis_key)
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No cached embedding for track {body.track_id} in room '{body.room}'. "
                "The face may have left the frame or the 90-second window has expired. "
                "Wait for the person to appear again and retry."
            ),
        )

    try:
        embedding: list[float] = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt embedding cache: {exc}") from exc

    # Load existing embeddings, add/overwrite the name, write back atomically.
    enrolled: dict[str, list[float]] = {}
    if EMBEDDINGS_PATH.exists():
        try:
            with open(EMBEDDINGS_PATH, "rb") as f:
                enrolled = pickle.load(f)  # noqa: S301 — local T0-derived file
        except (EOFError, pickle.UnpicklingError):
            logger.warning("embeddings.pkl corrupt — will overwrite")

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")

    if name in enrolled:
        # Average new embedding with existing one (improves match stability
        # when the same person is enrolled from multiple angles/lighting).
        import numpy as np  # noqa: PLC0415

        existing = np.asarray(enrolled[name], dtype="float32")
        new_emb = np.asarray(embedding, dtype="float32")
        mean = (existing + new_emb) / 2.0
        norm = float(np.linalg.norm(mean))
        enrolled[name] = (mean / norm if norm > 0 else mean).tolist()
        logger.info("Updated enrollment for '%s' (averaged)", name)
    else:
        enrolled[name] = embedding
        logger.info("New enrollment: '%s'", name)

    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EMBEDDINGS_PATH.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(enrolled, f)
    tmp.replace(EMBEDDINGS_PATH)

    # Hot-reload CV pipeline — SIGHUP triggers _load_models() which re-reads
    # embeddings.pkl without dropping the RTSP stream.
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

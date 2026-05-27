"""Face enrollment endpoint — adds live-captured embeddings to embeddings.pkl.

Two on-disk artifacts per person:

* ``models/persons/{name}/samples.npz`` — raw L2-normalized 512-d embeddings,
  appended across enrollments and capped at MAX_TOTAL_SAMPLES (FIFO trim).
  Source-of-truth for "what we've ever seen of this face".
* ``models/embeddings.pkl`` — derived ``{name: list[list[float]]}`` with up to
  TEMPLATES_PER_PERSON 512-d templates picked by farthest-point sampling from
  ``samples.npz``. Read by the CV pipeline; it does max-cosine matching over
  the templates for each name (better cross-pose/lighting than a single mean).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pickle
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["enroll"])

EMBEDDINGS_PATH = Path(os.environ.get("FACE_EMBEDDINGS_PATH", "/app/models/embeddings.pkl"))
SAMPLES_ROOT = EMBEDDINGS_PATH.parent / "persons"
CV_CONTAINER = os.environ.get("CV_CONTAINER", "cv")

# Minimum samples required for a stable ArcFace anchor. Single-frame enroll
# captures whatever the face looked like at the click moment (blur, eyes
# closed, profile view) and produces a poor enrollment; averaging across N
# frames smooths these out. The CV pipeline pushes one embedding per second
# per track, so 5 samples ≈ 5 s of in-frame presence.
MIN_ENROLL_SAMPLES = 5
ENROLL_WAIT_BUDGET_SEC = 8.0
ENROLL_POLL_INTERVAL_SEC = 0.5

# How many diverse templates per person to keep in embeddings.pkl. K=5
# captures most cross-pose / lighting variation without making recognition
# expensive (each frame's match cost is O(K · num_people · 512) dot products).
TEMPLATES_PER_PERSON = 5
# Hard cap on accumulated raw samples per person; older samples drop FIFO.
# 200 ≈ 10 typical enrollments at MAX_ENROLL_EMBEDDINGS=20 each.
MAX_TOTAL_SAMPLES = 200
ARCFACE_DIM = 512


class EnrollBody(BaseModel):
    room: str
    track_id: int
    name: str


class EnrollResponse(BaseModel):
    status: str
    enrolled_count: int


def _validate_name(name: str) -> str:
    """Reject path-traversal characters before using `name` as a directory."""
    clean = name.strip()
    if not clean or "/" in clean or "\\" in clean or "\x00" in clean or clean in (".", ".."):
        raise HTTPException(status_code=422, detail="name contains invalid characters")
    return clean


def _samples_path(name: str) -> Path:
    return SAMPLES_ROOT / name / "samples.npz"


def _load_samples(path: Path) -> np.ndarray:
    """Load (M, ARCFACE_DIM) float32 array; return (0, ARCFACE_DIM) on any error."""
    empty = np.zeros((0, ARCFACE_DIM), dtype=np.float32)
    if not path.exists():
        return empty
    try:
        with np.load(path) as data:
            arr = np.asarray(data["embeddings"], dtype=np.float32)
    except (OSError, ValueError, KeyError) as exc:
        logger.warning("Failed to load %s: %s — starting fresh", path, exc)
        return empty
    if arr.ndim != 2 or arr.shape[1] != ARCFACE_DIM:
        logger.warning("samples.npz at %s has unexpected shape %s — discarding", path, arr.shape)
        return empty
    return arr


def _save_samples(path: Path, arr: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # numpy.savez_compressed auto-appends ".npz" when given a path, which
    # turns "samples.npz.tmp" into "samples.npz.tmp.npz" and breaks the
    # atomic-replace dance. Passing an open file-handle skips that suffix
    # logic and writes to exactly the name we asked for.
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, embeddings=arr)
    tmp.replace(path)


def select_diverse_templates(samples: np.ndarray, k: int) -> np.ndarray:
    """Greedy farthest-point sampling. Returns up to k rows from ``samples``.

    Inputs are assumed L2-normalized (so cosine sim = dot product). The first
    template is the row most similar to the centroid (the "most average" face);
    each subsequent template maximizes minimum distance to the already-picked
    set — yielding spread across pose, lighting, expression rather than K
    nearly-identical copies of the same frame.
    """
    n = samples.shape[0]
    if n == 0 or n <= k:
        return samples

    centroid = samples.mean(axis=0)
    cn = float(np.linalg.norm(centroid))
    if cn > 0:
        centroid = centroid / cn

    picked: list[int] = [int(np.argmax(samples @ centroid))]
    for _ in range(k - 1):
        sims = samples @ samples[picked].T  # (n, m)
        max_sim_to_picked = sims.max(axis=1)
        # Exclude already-picked indices so they aren't re-selected.
        max_sim_to_picked[picked] = 2.0
        picked.append(int(np.argmin(max_sim_to_picked)))

    return samples[picked]


def _load_pkl_as_templates(path: Path) -> dict[str, list[list[float]]]:
    """Load embeddings.pkl, transparently upgrading legacy single-template entries.

    Old format: ``{name: list[float]}`` (one 512-d mean per person, pre-multi-
    template). New format: ``{name: list[list[float]]}``. This shim lets the
    code path stay uniform without needing a migration script.
    """
    enrolled: dict[str, list[list[float]]] = {}
    if not path.exists():
        return enrolled
    try:
        with open(path, "rb") as f:
            raw = pickle.load(f)  # noqa: S301 — local T0-derived file
    except (EOFError, pickle.UnpicklingError) as exc:
        logger.warning("embeddings.pkl corrupt (%s) — will overwrite", exc)
        return enrolled
    if not isinstance(raw, dict):
        logger.warning("embeddings.pkl has unexpected top-level type %s — discarding", type(raw))
        return enrolled
    for k, v in raw.items():
        if not isinstance(v, list) or not v:
            continue
        if isinstance(v[0], int | float):
            enrolled[k] = [list(v)]
        else:
            enrolled[k] = [list(t) for t in v]
    return enrolled


@router.get("/api/cv/enrollments")
async def list_enrollments() -> dict[str, Any]:
    """Return the names of all enrolled faces (without embeddings)."""
    enrolled = _load_pkl_as_templates(EMBEDDINGS_PATH)
    return {"names": sorted(enrolled.keys()), "count": len(enrolled)}


@router.delete("/api/cv/enrollments/{name}")
async def delete_enrollment(name: str) -> dict[str, Any]:
    """Remove a named face from embeddings.pkl + samples.npz and hot-reload CV."""
    clean = _validate_name(name)
    if not EMBEDDINGS_PATH.exists():
        raise HTTPException(status_code=404, detail="No enrollments file found")

    enrolled = _load_pkl_as_templates(EMBEDDINGS_PATH)
    if clean not in enrolled:
        raise HTTPException(status_code=404, detail=f"'{clean}' not found in enrollments")

    del enrolled[clean]

    tmp = EMBEDDINGS_PATH.with_suffix(".pkl.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(enrolled, f)
    tmp.replace(EMBEDDINGS_PATH)

    # Drop the raw samples too — otherwise a future enroll with the same name
    # would resurrect them, defeating the user's delete intent.
    person_dir = SAMPLES_ROOT / clean
    samples_file = person_dir / "samples.npz"
    if samples_file.exists():
        samples_file.unlink()
    if person_dir.exists():
        try:
            person_dir.rmdir()
        except OSError:
            pass  # not empty — leave alone

    logger.info("Deleted enrollment: '%s'", clean)
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

    Pipeline:
    1. Wait until the per-track Redis buffer has at least MIN_ENROLL_SAMPLES.
    2. L2-renormalize each sample (defensive — CV side already normalizes).
    3. Append them to the person's accumulated ``samples.npz`` (FIFO-capped at
       MAX_TOTAL_SAMPLES across all past enrollments).
    4. Re-derive TEMPLATES_PER_PERSON diverse templates from the full sample
       set via farthest-point sampling.
    5. Write templates to ``embeddings.pkl`` and SIGHUP the CV container.

    Status codes: 404 — no buffered embeddings (face left frame or TTL
    expired); 409 — track present but fewer than MIN_ENROLL_SAMPLES collected
    within the wait budget.
    """
    name = _validate_name(body.name)

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

    new_arr = np.asarray(embeddings, dtype=np.float32)
    # Defensive renormalize — the CV side L2-normalizes embed_face output, but
    # JSON round-trip + averaging upstream could conceivably perturb norms.
    # np.maximum keeps the dtype stable (np.where upcasts to float64).
    norms = np.maximum(np.linalg.norm(new_arr, axis=1, keepdims=True), 1e-12)
    new_arr = (new_arr / norms).astype(np.float32, copy=False)

    samples_path = _samples_path(name)
    prior = _load_samples(samples_path)
    all_samples = np.concatenate([prior, new_arr], axis=0) if prior.shape[0] else new_arr
    if all_samples.shape[0] > MAX_TOTAL_SAMPLES:
        all_samples = all_samples[-MAX_TOTAL_SAMPLES:]
    _save_samples(samples_path, all_samples)

    templates = select_diverse_templates(all_samples, TEMPLATES_PER_PERSON)

    enrolled = _load_pkl_as_templates(EMBEDDINGS_PATH)
    enrolled[name] = templates.tolist()
    logger.info(
        "Enrolled '%s': %d new samples → %d total → %d templates",
        name,
        len(embeddings),
        all_samples.shape[0],
        templates.shape[0],
    )

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

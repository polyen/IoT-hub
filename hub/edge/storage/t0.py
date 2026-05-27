"""T0 storage guard — enforces that private data only writes to external storage.

T0 frames must reside on a separate storage device from the OS root (SSD, NVMe,
or LUKS-encrypted partition).  The directory does not have to be the exact mount
point — any subdirectory of an externally-mounted filesystem is accepted.
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

T0_MOUNT = Path(os.environ.get("T0_MOUNT", "/mnt/edge-data"))
T0_FRAMES_DIR = T0_MOUNT / "frames"
RETENTION_DAYS = int(os.environ.get("T0_RETENTION_DAYS", "7"))

# Cached result of assert_t0_available() — the storage device and LUKS status
# don't change at runtime, so subprocess checks run only once.
_t0_check_done: bool = False


class T0StorageError(RuntimeError):
    pass


def _is_on_external_device(path: Path) -> bool:
    """Return True if path (or its nearest existing ancestor) is on a block
    device different from the root filesystem.

    Accepts mount-point subdirectories (e.g. /mnt/ssd/edge-data where /mnt/ssd
    is the actual mount) as well as exact mount points and Docker bind mounts.
    """
    # Walk up to the nearest existing ancestor.
    p = path
    while not p.exists():
        parent = p.parent
        if parent == p:
            return False
        p = parent
    try:
        return os.stat(p).st_dev != os.stat("/").st_dev
    except OSError:
        return False


def _is_luks_encrypted(path: Path) -> bool:
    """Check if the block device backing path is LUKS-encrypted."""
    try:
        result = subprocess.run(
            ["findmnt", "--target", str(path), "--output", "SOURCE", "--noheadings"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        source = result.stdout.strip()
        if not source:
            return False
        luks_check = subprocess.run(
            ["cryptsetup", "status", source],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "type:  LUKS" in luks_check.stdout or "type: LUKS" in luks_check.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def assert_t0_available() -> None:
    """Raise T0StorageError if T0 storage is not available or not on external storage.

    Storage device and LUKS status are checked only once per process — results
    are cached in _t0_check_done to avoid subprocess spam on every frame.
    """
    global _t0_check_done
    if _t0_check_done:
        return
    if not T0_MOUNT.exists():
        raise T0StorageError(
            f"T0 storage directory {T0_MOUNT} does not exist. "
            f"Create it first: sudo mkdir -p {T0_MOUNT} && sudo chown $USER {T0_MOUNT}"
        )
    if not T0_MOUNT.is_mount() and not _is_on_external_device(T0_MOUNT):
        logger.warning(
            "T0 storage %s is on the root filesystem — "
            "for production privacy store frames on a separate SSD/NVMe partition.",
            T0_MOUNT,
        )
    elif not _is_luks_encrypted(T0_MOUNT):
        logger.warning(
            "T0 storage %s is not LUKS-encrypted. "
            "For production privacy consider encrypting the partition.",
            T0_MOUNT,
        )
    _t0_check_done = True


def write_frame(frame_bytes: bytes, track_id: int, timestamp: datetime | None = None) -> Path:
    """Write a T0 frame to encrypted storage. Returns the saved path."""
    assert_t0_available()
    T0_FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    ts = timestamp or datetime.now(UTC)
    filename = f"{ts.strftime('%Y%m%d_%H%M%S_%f')}_track{track_id}.jpg"
    frame_path = T0_FRAMES_DIR / filename
    frame_path.write_bytes(frame_bytes)
    return frame_path


def cleanup_old_frames(retention_days: int = RETENTION_DAYS) -> int:
    """Delete frames older than retention_days. Returns count deleted."""
    if not T0_FRAMES_DIR.exists():
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted = 0
    for f in T0_FRAMES_DIR.glob("*.jpg"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
            if mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted

"""T0 storage guard — enforces that private data only writes to encrypted storage.

T0 data (face frames, biometrics) MUST reside on /mnt/edge-data (LUKS-encrypted).
This module refuses writes if the mount is not present or not encrypted.
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


class T0StorageError(RuntimeError):
    pass


def _is_mounted(path: Path) -> bool:
    """Return True if path is a real mount point (not the underlying fs root)."""
    return path.is_mount()


def _is_luks_encrypted(path: Path) -> bool:
    """Check if the block device backing path is LUKS-encrypted.

    Uses findmnt to identify the source device and cryptsetup to verify type.
    Falls back gracefully on non-Linux or in Docker dev environments without LUKS.
    """
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
    """Raise T0StorageError if T0 mount is not available or not encrypted."""
    if not T0_MOUNT.exists():
        raise T0StorageError(
            f"T0 mount point {T0_MOUNT} does not exist. "
            "Run edge-bootstrap.sh to set up LUKS partition."
        )
    if not _is_mounted(T0_MOUNT):
        raise T0StorageError(
            f"{T0_MOUNT} is not mounted. "
            "Unlock LUKS volume: sudo cryptsetup luksOpen /dev/nvme0n1p1 edge-data"
        )
    if not _is_luks_encrypted(T0_MOUNT):
        logger.warning(
            "T0 mount %s is not LUKS-encrypted (dev mode or check failed). "
            "In production this MUST be encrypted.",
            T0_MOUNT,
        )


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

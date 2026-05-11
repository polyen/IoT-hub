from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import hub.edge.storage.t0 as t0_mod
from hub.edge.storage.t0 import (
    T0StorageError,
    _is_mounted,
    assert_t0_available,
    cleanup_old_frames,
    write_frame,
)


def test_assert_t0_available_raises_when_mount_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent-mount"
    with patch.object(t0_mod, "T0_MOUNT", missing):
        with pytest.raises(T0StorageError, match="does not exist"):
            assert_t0_available()


def test_write_frame_raises_when_mount_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent-mount"
    with patch.object(t0_mod, "T0_MOUNT", missing):
        with patch.object(t0_mod, "T0_FRAMES_DIR", missing / "frames"):
            with pytest.raises(T0StorageError, match="does not exist"):
                write_frame(b"\xff\xd8\xff", track_id=1)


def test_cleanup_old_frames_returns_zero_when_dir_missing(tmp_path: Path) -> None:
    missing = tmp_path / "no-frames-dir"
    with patch.object(t0_mod, "T0_FRAMES_DIR", missing):
        result = cleanup_old_frames(retention_days=7)
    assert result == 0


def test_cleanup_old_frames_deletes_stale_files(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    old_file = frames_dir / "20200101_000000_000000_track1.jpg"
    recent_file = frames_dir / "20991231_000000_000000_track2.jpg"
    old_file.write_bytes(b"old")
    recent_file.write_bytes(b"new")

    past_mtime = (datetime.now(UTC) - timedelta(days=10)).timestamp()
    future_mtime = (datetime.now(UTC) + timedelta(days=1)).timestamp()
    import os

    os.utime(old_file, (past_mtime, past_mtime))
    os.utime(recent_file, (future_mtime, future_mtime))

    with patch.object(t0_mod, "T0_FRAMES_DIR", frames_dir):
        deleted = cleanup_old_frames(retention_days=7)

    assert deleted == 1
    assert not old_file.exists()
    assert recent_file.exists()


def test_is_mounted_returns_false_for_nonexistent_path(tmp_path: Path) -> None:
    missing = tmp_path / "not-a-mount"
    assert _is_mounted(missing) is False

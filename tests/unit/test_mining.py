"""Unit tests for training.mining (hard-negative miner)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from training.mining import (
    balance_by_tag,
    copy_frame,
    find_next_version,
    write_yolo_label,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(tag: str | None = None) -> MagicMock:
    """Build a minimal FeedbackEvent-like mock."""
    ev = MagicMock()
    ev.tag = tag
    ev.user_label = "fp"
    ev.ts = datetime.now(UTC)
    ev.frame_blob_ref = None
    ev.id = uuid.uuid4()
    ev.alert_id = uuid.uuid4()
    return ev


# ---------------------------------------------------------------------------
# test_balance_by_tag
# ---------------------------------------------------------------------------


def test_balance_by_tag_caps_at_max_per_tag() -> None:
    """150 events with tag='fire' → capped to 100; 50 with tag='smoke' → kept."""
    fire_events = [_make_event(tag="fire") for _ in range(150)]
    smoke_events = [_make_event(tag="smoke") for _ in range(50)]
    all_events = fire_events + smoke_events

    result = balance_by_tag(all_events, max_per_tag=100)

    fire_kept = [e for e in result if e.tag == "fire"]
    smoke_kept = [e for e in result if e.tag == "smoke"]

    assert len(fire_kept) == 100
    assert len(smoke_kept) == 50
    assert len(result) == 150


def test_balance_by_tag_none_grouped_as_untagged() -> None:
    """Events with tag=None are grouped together under 'untagged' bucket."""
    events = [_make_event(tag=None) for _ in range(120)]
    result = balance_by_tag(events, max_per_tag=100)
    assert len(result) == 100


def test_balance_by_tag_empty_input() -> None:
    assert balance_by_tag([], max_per_tag=100) == []


def test_balance_by_tag_exactly_at_limit() -> None:
    """Exactly max_per_tag events per tag → all kept."""
    events = [_make_event(tag="fire") for _ in range(100)]
    result = balance_by_tag(events, max_per_tag=100)
    assert len(result) == 100


# ---------------------------------------------------------------------------
# test_find_next_version
# ---------------------------------------------------------------------------


def test_find_next_version_empty_dir(tmp_path: Path) -> None:
    """No existing feedback_v* dirs → version 1."""
    base = tmp_path / "datasets" / "fire_smoke"
    base.mkdir(parents=True)
    assert find_next_version(base) == 1


def test_find_next_version_with_existing(tmp_path: Path) -> None:
    """Existing feedback_v2/ → next is 3."""
    base = tmp_path / "datasets" / "fire_smoke"
    (base / "feedback_v2").mkdir(parents=True)
    assert find_next_version(base) == 3


def test_find_next_version_multiple(tmp_path: Path) -> None:
    """feedback_v1 and feedback_v3 exist → next is 4."""
    base = tmp_path / "ds"
    (base / "feedback_v1").mkdir(parents=True)
    (base / "feedback_v3").mkdir(parents=True)
    assert find_next_version(base) == 4


def test_find_next_version_base_not_exists(tmp_path: Path) -> None:
    """Base dir doesn't exist yet → version 1."""
    base = tmp_path / "nonexistent"
    assert find_next_version(base) == 1


def test_find_next_version_ignores_non_version_dirs(tmp_path: Path) -> None:
    """Directories that don't match feedback_v<int> are ignored."""
    base = tmp_path / "ds"
    (base / "feedback_vX").mkdir(parents=True)
    (base / "other_dir").mkdir(parents=True)
    (base / "feedback_v2").mkdir(parents=True)
    assert find_next_version(base) == 3


# ---------------------------------------------------------------------------
# test_copy_frame_missing
# ---------------------------------------------------------------------------


def test_copy_frame_missing_returns_false(tmp_path: Path) -> None:
    """Non-existent frame_ref → copy_frame returns False without raising."""
    missing_ref = str(tmp_path / "nonexistent_dir" / "frame_001.jpg")
    dest = tmp_path / "dest"
    result = copy_frame(missing_ref, dest)
    assert result is False


def test_copy_frame_copies_file(tmp_path: Path) -> None:
    """Existing file → copy_frame returns True and file exists in dest."""
    src = tmp_path / "frame_001.jpg"
    src.write_bytes(b"\xff\xd8\xff")
    dest = tmp_path / "dest"

    result = copy_frame(str(src), dest)

    assert result is True
    assert (dest / "frame_001.jpg").exists()
    assert (dest / "frame_001.jpg").read_bytes() == b"\xff\xd8\xff"


# ---------------------------------------------------------------------------
# test_write_yolo_label
# ---------------------------------------------------------------------------


def test_write_yolo_label_creates_correct_file(tmp_path: Path) -> None:
    """write_yolo_label creates .txt with YOLO placeholder format."""
    images_dir = tmp_path / "images"
    labels_dir = tmp_path / "labels"
    images_dir.mkdir()

    image_path = images_dir / "frame_001.jpg"
    image_path.write_bytes(b"\xff\xd8\xff")

    write_yolo_label(image_path, labels_dir)

    label_file = labels_dir / "frame_001.txt"
    assert label_file.exists()
    content = label_file.read_text(encoding="utf-8").strip()
    parts = content.split()
    assert len(parts) == 5, f"Expected 5 parts in YOLO label, got: {content!r}"
    assert parts[0] == "0"  # class id
    assert float(parts[1]) == pytest.approx(0.5)  # cx
    assert float(parts[2]) == pytest.approx(0.5)  # cy
    assert float(parts[3]) == pytest.approx(1.0)  # w
    assert float(parts[4]) == pytest.approx(1.0)  # h


def test_write_yolo_label_creates_label_dir(tmp_path: Path) -> None:
    """write_yolo_label creates labels_dir if it doesn't exist."""
    image_path = tmp_path / "frame_002.jpg"
    image_path.write_bytes(b"")
    labels_dir = tmp_path / "new_labels_dir"

    assert not labels_dir.exists()
    write_yolo_label(image_path, labels_dir)
    assert labels_dir.exists()
    assert (labels_dir / "frame_002.txt").exists()

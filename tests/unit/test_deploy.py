"""Unit tests for hub.edge.mlops.deploy — ModelStore promote/rollback."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hub.edge.mlops.deploy import ChecksumMismatchError, ModelStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path, kind: str = "yolo") -> ModelStore:
    """Return a ModelStore rooted at tmp_path."""
    return ModelStore(models_dir=tmp_path, kind=kind)


def _touch_hef(directory: Path, stem: str, body: bytes = b"FAKE_HEF") -> Path:
    """Create a fake <stem>.hef file in versions/ and return its path."""
    versions = directory / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    p = versions / f"{stem}.hef"
    p.write_bytes(body)
    return p


def _write_manifest(directory: Path, entries: dict[str, dict[str, str]]) -> None:
    """Write manifest.json with the given entries."""
    (directory / "manifest.json").write_text(json.dumps(entries))


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


def test_promote_creates_symlink(tmp_path: Path) -> None:
    """promote('v1') should create current_yolo.hef → versions/v1.hef + legacy alias."""
    _touch_hef(tmp_path, "v1")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")

    active = tmp_path / "current_yolo.hef"
    assert active.is_symlink(), "current_yolo.hef should be a symlink after promote"
    assert active.resolve() == (tmp_path / "versions" / "v1.hef").resolve()

    # Legacy alias still works for back-compat with pipeline.py + old callers.
    legacy = tmp_path / "current.hef"
    assert legacy.is_symlink()
    assert legacy.resolve() == (tmp_path / "versions" / "v1.hef").resolve()

    # docker kill --signal=SIGHUP cv
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "docker" in call_args
    assert any("SIGHUP" in arg for arg in call_args)
    assert "cv" in call_args


def test_promote_missing_version(tmp_path: Path) -> None:
    """promote() raises FileNotFoundError for a non-existent version."""
    store = _make_store(tmp_path)

    with pytest.raises(FileNotFoundError, match="nonexistent"):
        store.promote("nonexistent")


def test_promote_writes_history(tmp_path: Path) -> None:
    """promote() appends a record to deployments.json."""
    _touch_hef(tmp_path, "v1")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")

    history_path = tmp_path / "deployments.json"
    assert history_path.is_file()
    history = json.loads(history_path.read_text())
    assert len(history) == 1
    assert history[0]["kind"] == "yolo"
    assert history[0]["version"] == "v1"
    assert history[0]["rolled_back"] is False


def test_promote_verifies_sha256(tmp_path: Path) -> None:
    """promote() raises ChecksumMismatchError when manifest sha256 disagrees."""
    body = b"FAKE_HEF"
    _touch_hef(tmp_path, "v1", body=body)
    _write_manifest(tmp_path, {"v1": {"sha256": "deadbeef" * 8, "kind": "yolo"}})

    store = _make_store(tmp_path)
    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        with pytest.raises(ChecksumMismatchError, match="SHA256 mismatch"):
            store.promote("v1")
    # Symlink must NOT have been created when checksum fails.
    assert not (tmp_path / "current_yolo.hef").exists()


def test_promote_accepts_matching_sha256(tmp_path: Path) -> None:
    body = b"FAKE_HEF_PAYLOAD"
    _touch_hef(tmp_path, "v1", body=body)
    digest = hashlib.sha256(body).hexdigest()
    _write_manifest(tmp_path, {"v1": {"sha256": digest, "kind": "yolo"}})

    store = _make_store(tmp_path)
    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")
    assert (tmp_path / "current_yolo.hef").is_symlink()


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def test_rollback_returns_previous_from_history(tmp_path: Path) -> None:
    """promote v1, promote v2, rollback → v1 (history-based)."""
    _touch_hef(tmp_path, "v1")
    _touch_hef(tmp_path, "v2")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")
        store.promote("v2")

    assert store.current_version() == "v2"

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = store.rollback()

    assert result == "v1"
    active = tmp_path / "current_yolo.hef"
    assert active.is_symlink()
    assert active.resolve() == (tmp_path / "versions" / "v1.hef").resolve()


def test_rollback_disk_fallback_when_no_history(tmp_path: Path) -> None:
    """With only one history entry and a sibling .hef on disk, rollback picks the sibling."""
    _touch_hef(tmp_path, "v1")
    _touch_hef(tmp_path, "v2")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v2")

    assert store.current_version() == "v2"

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = store.rollback()

    assert result == "v1"
    assert (tmp_path / "current_yolo.hef").resolve() == (tmp_path / "versions" / "v1.hef").resolve()


def test_rollback_no_prior(tmp_path: Path) -> None:
    """With only v1.hef as current, rollback() returns None."""
    _touch_hef(tmp_path, "v1")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")

    assert store.current_version() == "v1"
    assert store.rollback() is None


def test_rollback_marks_history_entry(tmp_path: Path) -> None:
    """After rollback the latest promote entry is marked rolled_back=True."""
    _touch_hef(tmp_path, "v1")
    _touch_hef(tmp_path, "v2")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")
        store.promote("v2")
        store.rollback()

    history = json.loads((tmp_path / "deployments.json").read_text())
    # v2 promote entry must be flagged as rolled_back; the v1 re-promote that
    # rollback() performed adds a fresh entry on top.
    v2_entries = [h for h in history if h["version"] == "v2"]
    assert v2_entries and v2_entries[-1]["rolled_back"] is True


# ---------------------------------------------------------------------------
# multi-kind
# ---------------------------------------------------------------------------


def test_multi_kind_independent_symlinks(tmp_path: Path) -> None:
    """yolo and pose ModelStores write distinct current_*.hef symlinks."""
    _touch_hef(tmp_path, "yolo_v1")
    _touch_hef(tmp_path, "pose_v1")

    yolo = _make_store(tmp_path, kind="yolo")
    pose = _make_store(tmp_path, kind="pose")

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        yolo.promote("yolo_v1")
        pose.promote("pose_v1")

    assert (tmp_path / "current_yolo.hef").resolve() == (
        tmp_path / "versions" / "yolo_v1.hef"
    ).resolve()
    assert (tmp_path / "current_pose.hef").resolve() == (
        tmp_path / "versions" / "pose_v1.hef"
    ).resolve()
    # Legacy alias only follows yolo
    assert (tmp_path / "current.hef").resolve() == (tmp_path / "versions" / "yolo_v1.hef").resolve()


def test_unknown_kind_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown model kind"):
        ModelStore(models_dir=tmp_path, kind="bogus")

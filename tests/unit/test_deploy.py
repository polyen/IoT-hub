"""Unit tests for hub.edge.mlops.deploy — ModelStore promote/rollback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hub.edge.mlops.deploy import ModelStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> ModelStore:
    """Return a ModelStore rooted at tmp_path."""
    return ModelStore(models_dir=tmp_path)


def _touch_hef(directory: Path, stem: str) -> Path:
    """Create a fake <stem>.hef file and return its path."""
    p = directory / f"{stem}.hef"
    p.write_bytes(b"FAKE_HEF")
    return p


# ---------------------------------------------------------------------------
# test_promote_creates_symlink
# ---------------------------------------------------------------------------


def test_promote_creates_symlink(tmp_path: Path) -> None:
    """promote('v1') should create current.hef → v1.hef symlink."""
    _touch_hef(tmp_path, "v1")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")

    active = tmp_path / "current.hef"
    assert active.is_symlink(), "current.hef should be a symlink after promote"
    assert active.resolve() == (tmp_path / "v1.hef").resolve()

    # Ensure docker kill was called with SIGHUP
    mock_run.assert_called_once()
    call_args = mock_run.call_args[0][0]
    assert "docker" in call_args
    assert any("SIGHUP" in arg for arg in call_args)


# ---------------------------------------------------------------------------
# test_promote_missing_version
# ---------------------------------------------------------------------------


def test_promote_missing_version(tmp_path: Path) -> None:
    """promote() should raise FileNotFoundError for a non-existent version."""
    store = _make_store(tmp_path)

    with pytest.raises(FileNotFoundError, match="nonexistent"):
        store.promote("nonexistent")


# ---------------------------------------------------------------------------
# test_rollback_returns_previous
# ---------------------------------------------------------------------------


def test_rollback_returns_previous(tmp_path: Path) -> None:
    """With v1.hef + v2.hef and current=v2, rollback() promotes v1."""
    _touch_hef(tmp_path, "v1")
    _touch_hef(tmp_path, "v2")
    store = _make_store(tmp_path)

    # First promote v2 so it is the current active version
    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v2")

    assert store.current_version() == "v2"

    # Now rollback — should promote v1
    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = store.rollback()

    assert result == "v1"
    # Verify promote was called for v1 (current.hef points to v1.hef)
    active = tmp_path / "current.hef"
    assert active.is_symlink()
    assert active.resolve() == (tmp_path / "v1.hef").resolve()


# ---------------------------------------------------------------------------
# test_rollback_no_prior
# ---------------------------------------------------------------------------


def test_rollback_no_prior(tmp_path: Path) -> None:
    """With only v1.hef as current, rollback() returns None."""
    _touch_hef(tmp_path, "v1")
    store = _make_store(tmp_path)

    with patch("hub.edge.mlops.deploy.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        store.promote("v1")

    assert store.current_version() == "v1"

    # rollback should find no other candidates
    result = store.rollback()
    assert result is None

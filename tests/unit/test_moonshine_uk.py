"""Unit tests for the Ukrainian Moonshine ONNX STT backend + its selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import hub.edge.voice.hailo_whisper as hw
import hub.edge.voice.moonshine_stt as ms

# ---------------------------------------------------------------------------
# moonshine_uk_available — file/flag gating
# ---------------------------------------------------------------------------


def test_moonshine_uk_unavailable_when_dir_none() -> None:
    assert ms.moonshine_uk_available(None) is False


def test_moonshine_uk_unavailable_when_deps_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with all files present, missing moonshine/tokenizers → unavailable."""
    for name in ("encoder_model.onnx", "decoder_model_merged.onnx", "tokenizer.json"):
        (tmp_path / name).write_text("x")
    monkeypatch.setattr(ms, "MOONSHINE_AVAILABLE", True)
    monkeypatch.setattr(ms, "TOKENIZERS_AVAILABLE", False)
    assert ms.moonshine_uk_available(tmp_path) is False


def test_moonshine_uk_available_when_all_files_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ms, "MOONSHINE_AVAILABLE", True)
    monkeypatch.setattr(ms, "TOKENIZERS_AVAILABLE", True)
    for name in ("encoder_model.onnx", "decoder_model_merged.onnx", "tokenizer.json"):
        (tmp_path / name).write_text("x")
    assert ms.moonshine_uk_available(tmp_path) is True


def test_moonshine_uk_unavailable_when_a_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ms, "MOONSHINE_AVAILABLE", True)
    monkeypatch.setattr(ms, "TOKENIZERS_AVAILABLE", True)
    # decoder_model_merged.onnx deliberately absent
    (tmp_path / "encoder_model.onnx").write_text("x")
    (tmp_path / "tokenizer.json").write_text("x")
    assert ms.moonshine_uk_available(tmp_path) is False


# ---------------------------------------------------------------------------
# get_backend — selection prefers Moonshine-uk, falls back gracefully
# ---------------------------------------------------------------------------


def test_get_backend_prefers_moonshine_uk_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = MagicMock(name="MoonshineUkBackend-instance")
    monkeypatch.setenv("MOONSHINE_ONNX_DIR", "/models/moonshine-base-uk-onnx")
    monkeypatch.setattr(ms, "moonshine_uk_available", lambda _dir: True)
    monkeypatch.setattr(ms, "MoonshineUkBackend", lambda _dir: sentinel)

    backend = hw.get_backend(force_cpu=True)

    assert backend is sentinel


def test_get_backend_falls_back_when_moonshine_uk_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken Moonshine-uk load must not crash — STT falls back to faster-whisper."""
    monkeypatch.setenv("MOONSHINE_ONNX_DIR", "/models/bogus")
    monkeypatch.setattr(ms, "moonshine_uk_available", lambda _dir: True)

    def _boom(_dir: str) -> object:
        raise RuntimeError("corrupt onnx")

    monkeypatch.setattr(ms, "MoonshineUkBackend", _boom)

    fake_fw = MagicMock(name="FasterWhisperBackend-instance")
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(hw, "FasterWhisperBackend", lambda **_kw: fake_fw)

    backend = hw.get_backend(force_cpu=True)

    assert backend is fake_fw

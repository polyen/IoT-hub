"""Unit tests for Hailo Whisper STT backend, asset cache, and NPU scheduler."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# whisper_preprocess (imported transitively by hailo_whisper) needs scipy, which
# ships only with the optional ``voice`` extra — skip this module on CI's --extra dev.
pytest.importorskip("scipy")

import hub.edge.voice.hailo_whisper as hw  # noqa: E402

# ---------------------------------------------------------------------------
# FasterWhisperBackend defaults
# ---------------------------------------------------------------------------


def test_faster_whisper_default_model_is_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """After moving from large-v3-turbo to base, FasterWhisperBackend default follows."""
    fake_model = MagicMock()
    monkeypatch.setattr(hw, "WhisperModel", fake_model)
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)

    hw.FasterWhisperBackend()

    fake_model.assert_called_once()
    args, kwargs = fake_model.call_args
    assert (args[0] if args else kwargs.get("model_size_or_path")) == "base"


def test_faster_whisper_custom_model_size(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = MagicMock()
    monkeypatch.setattr(hw, "WhisperModel", fake_model)
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)

    hw.FasterWhisperBackend(model_size="small")
    assert fake_model.call_args[0][0] == "small"


def test_faster_whisper_language_default_is_uk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw, "WhisperModel", MagicMock())
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)

    backend = hw.FasterWhisperBackend()
    assert backend._language == "uk"


# ---------------------------------------------------------------------------
# get_backend() routing
# ---------------------------------------------------------------------------


def test_get_backend_falls_back_to_cpu_without_hailo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw, "WhisperModel", MagicMock())
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(hw, "HAILO_AVAILABLE", False)

    backend = hw.get_backend()
    assert isinstance(backend, hw.FasterWhisperBackend)


def test_get_backend_force_cpu_skips_hailo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw, "WhisperModel", MagicMock())
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(hw, "HAILO_AVAILABLE", True)
    monkeypatch.setattr(hw, "TRANSFORMERS_AVAILABLE", True)

    backend = hw.get_backend(force_cpu=True)
    assert isinstance(backend, hw.FasterWhisperBackend)


def test_get_backend_falls_back_when_transformers_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hw, "WhisperModel", MagicMock())
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(hw, "HAILO_AVAILABLE", True)
    monkeypatch.setattr(hw, "TRANSFORMERS_AVAILABLE", False)

    backend = hw.get_backend()
    assert isinstance(backend, hw.FasterWhisperBackend)


def test_get_backend_uses_hailo_when_opted_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """STT_BACKEND=hailo opts into the NPU path when assets resolve."""
    monkeypatch.setenv("STT_BACKEND", "hailo")
    monkeypatch.setattr(hw, "WhisperModel", MagicMock())
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(hw, "HAILO_AVAILABLE", True)
    monkeypatch.setattr(hw, "TRANSFORMERS_AVAILABLE", True)

    sentinel = object()
    with (
        patch.object(hw, "ensure_assets", return_value="ASSETS"),
        patch.object(hw, "HailoWhisperBackend", return_value=sentinel) as ctor,
    ):
        backend = hw.get_backend(assets_cache_dir=tmp_path, variant="tiny")

    assert backend is sentinel
    ctor.assert_called_once_with("ASSETS", language="uk")


def test_get_backend_default_avoids_hailo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default (auto) keeps STT on CPU even when Hailo is fully available."""
    monkeypatch.delenv("STT_BACKEND", raising=False)
    monkeypatch.setattr(hw, "WhisperModel", MagicMock())
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(hw, "HAILO_AVAILABLE", True)
    monkeypatch.setattr(hw, "TRANSFORMERS_AVAILABLE", True)

    with patch.object(hw, "ensure_assets", return_value="ASSETS") as ensure:
        backend = hw.get_backend(assets_cache_dir=tmp_path, variant="tiny")

    assert isinstance(backend, hw.FasterWhisperBackend)
    ensure.assert_not_called()  # NPU path never touched by default


def test_get_backend_hailo_opt_in_falls_through_when_init_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STT_BACKEND", "hailo")
    monkeypatch.setattr(hw, "WhisperModel", MagicMock())
    monkeypatch.setattr(hw, "FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(hw, "HAILO_AVAILABLE", True)
    monkeypatch.setattr(hw, "TRANSFORMERS_AVAILABLE", True)

    with patch.object(hw, "ensure_assets", side_effect=RuntimeError("no net")):
        backend = hw.get_backend(assets_cache_dir=tmp_path)

    assert isinstance(backend, hw.FasterWhisperBackend)


# ---------------------------------------------------------------------------
# whisper_assets — cache behaviour
# ---------------------------------------------------------------------------


def test_ensure_assets_returns_expected_paths(tmp_path: Path) -> None:
    """Cached files (size > 0) skip network; returned paths reflect the layout."""
    from hub.edge.voice import whisper_assets as wa

    enc = tmp_path / "hef" / "tiny-whisper-encoder-10s_15dB.hef"
    dec = tmp_path / "hef" / "tiny-whisper-decoder-fixed-sequence-matmul-split.hef"
    tok = tmp_path / "npy" / "token_embedding_weight_tiny.npy"
    add = tmp_path / "npy" / "onnx_add_input_tiny.npy"
    mel = tmp_path / "mel" / "mel_filters.npz"
    for p in (enc, dec, tok, add, mel):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"non-empty")

    with patch("urllib.request.urlopen") as opener:
        assets = wa.ensure_assets("tiny", tmp_path)

    # All five assets were already present → no socket opened.
    opener.assert_not_called()
    assert assets.encoder_hef == enc
    assert assets.decoder_hef == dec
    assert assets.chunk_seconds == 10
    assert assets.variant == "tiny"


def test_ensure_assets_rejects_unknown_variant(tmp_path: Path) -> None:
    from hub.edge.voice import whisper_assets as wa

    with pytest.raises(ValueError, match="Unsupported"):
        wa.ensure_assets("large", tmp_path)


# ---------------------------------------------------------------------------
# whisper_preprocess — mel shape (matches Hailo encoder HEF expectation)
# ---------------------------------------------------------------------------


_MEL_URL = (
    "https://raw.githubusercontent.com/hailocs/hailo-whisper/main/common/assets/mel_filters.npz"
)


@pytest.fixture(scope="module")
def mel_filters_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import urllib.error
    import urllib.request

    dest = tmp_path_factory.mktemp("mel") / "mel_filters.npz"
    try:
        urllib.request.urlretrieve(_MEL_URL, dest)
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"mel filters unreachable: {exc}")
    return dest


def test_audio_to_mel_shape_10s(mel_filters_path: Path) -> None:
    """10 s @ 16 kHz must produce exactly 1000 mel frames (encoder HEF requirement)."""
    from hub.edge.voice import whisper_preprocess as wp

    t = np.linspace(0, 3.0, 3 * 16000, endpoint=False, dtype=np.float32)
    audio = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    mel = wp.audio_to_mel(audio, chunk_seconds=10, mel_filters_path=str(mel_filters_path))
    assert mel.shape == (1, 1, 1000, 80)
    assert mel.dtype == np.float32


def test_audio_to_mel_shape_5s(mel_filters_path: Path) -> None:
    """5 s window (base variant) → 500 frames."""
    from hub.edge.voice import whisper_preprocess as wp

    audio = np.zeros(2 * 16000, dtype=np.float32)
    mel = wp.audio_to_mel(audio, chunk_seconds=5, mel_filters_path=str(mel_filters_path))
    assert mel.shape == (1, 1, 500, 80)


# ---------------------------------------------------------------------------
# NPUScheduler
# ---------------------------------------------------------------------------


def test_scheduler_whisper_waits_cv_sets_idle() -> None:
    from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy

    scheduler = NPUScheduler(NPUStrategy.WHISPER_WAITS)
    assert scheduler._cv_idle.is_set()

    async def _run() -> None:
        async with scheduler.cv_frame():
            assert not scheduler._cv_idle.is_set()
        assert scheduler._cv_idle.is_set()

    asyncio.run(_run())


def test_scheduler_whisper_waits_acquires_after_cv() -> None:
    from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy

    scheduler = NPUScheduler(NPUStrategy.WHISPER_WAITS)
    order: list[str] = []

    async def _run() -> None:
        async def cv_task() -> None:
            async with scheduler.cv_frame():
                order.append("cv_start")
                await asyncio.sleep(0.01)
                order.append("cv_end")

        async def whisper_task() -> None:
            await asyncio.sleep(0.001)
            async with scheduler.whisper_inference():
                order.append("whisper")

        await asyncio.gather(cv_task(), whisper_task())

    asyncio.run(_run())
    assert order == ["cv_start", "cv_end", "whisper"]


def test_scheduler_preempt_whisper_gets_npu_after_cv_yields() -> None:
    from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy

    scheduler = NPUScheduler(NPUStrategy.PREEMPT)
    order: list[str] = []

    async def _run() -> None:
        async def cv_loop() -> None:
            for _ in range(2):
                async with scheduler.cv_frame():
                    order.append("cv")
                    await asyncio.sleep(0.005)

        async def whisper_task() -> None:
            await asyncio.sleep(0.001)
            async with scheduler.whisper_inference():
                order.append("whisper")

        await asyncio.gather(cv_loop(), whisper_task())

    asyncio.run(_run())
    assert "whisper" in order and "cv" in order


def test_scheduler_tracks_acquisitions() -> None:
    from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy

    scheduler = NPUScheduler(NPUStrategy.WHISPER_WAITS)

    async def _run() -> None:
        async with scheduler.whisper_inference():
            pass
        async with scheduler.whisper_inference():
            pass

    asyncio.run(_run())
    assert scheduler.stats().whisper_acquisitions == 2

"""Unit tests for Hailo Whisper STT backend and NPU scheduler (Phase 5)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_faster_whisper() -> MagicMock:
    stub = MagicMock()
    stub.WhisperModel = MagicMock()
    return stub


def _reload_hailo_whisper(
    faster_whisper_stub: MagicMock | None = None,
    hailo_available: bool = False,
) -> object:
    import importlib

    mods: dict[str, object] = {}
    if faster_whisper_stub is not None:
        mods["faster_whisper"] = faster_whisper_stub
    if not hailo_available:
        mods["hailo_platform"] = None  # ensure ImportError path

    # Remove cached modules so reload picks up stubs
    for mod in ["hub.edge.voice.hailo_whisper"]:
        sys.modules.pop(mod, None)

    with patch.dict(sys.modules, mods):
        import hub.edge.voice.hailo_whisper as hw  # noqa: PLC0415

        importlib.reload(hw)
        if faster_whisper_stub is not None:
            hw.FASTER_WHISPER_AVAILABLE = True
            hw.WhisperModel = faster_whisper_stub.WhisperModel
        hw.HAILO_AVAILABLE = hailo_available
        return hw


# ---------------------------------------------------------------------------
# FasterWhisperBackend — model upgrade to large-v3-turbo
# ---------------------------------------------------------------------------


def test_faster_whisper_default_model_is_turbo() -> None:
    """FasterWhisperBackend default model must be large-v3-turbo after Phase 5."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub)

    hw.FasterWhisperBackend()  # type: ignore[attr-defined]

    stub.WhisperModel.assert_called_once()
    args, kwargs = stub.WhisperModel.call_args
    model_size = args[0] if args else kwargs.get("model_size_or_path")
    assert model_size == "large-v3-turbo", f"Expected large-v3-turbo, got {model_size}"


def test_faster_whisper_custom_model_size() -> None:
    """FasterWhisperBackend should accept a custom model_size override."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub)

    hw.FasterWhisperBackend(model_size="small")  # type: ignore[attr-defined]

    args, _ = stub.WhisperModel.call_args
    assert args[0] == "small"


def test_faster_whisper_language_param() -> None:
    """FasterWhisperBackend default language must be 'uk'."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub)

    backend = hw.FasterWhisperBackend()  # type: ignore[attr-defined]
    assert backend._language == "uk"


# ---------------------------------------------------------------------------
# get_backend() routing
# ---------------------------------------------------------------------------


def test_get_backend_uses_hailo_when_available(tmp_path: Path) -> None:
    """get_backend() must return HailoWhisperBackend when HEF exists + Hailo available."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub, hailo_available=True)

    hef = tmp_path / "whisper.hef"
    hef.write_bytes(b"fake")

    with patch.object(hw.HailoWhisperBackend, "load"):  # type: ignore[attr-defined]
        backend = hw.get_backend(hef_path=hef)  # type: ignore[attr-defined]

    assert isinstance(backend, hw.HailoWhisperBackend)  # type: ignore[attr-defined]


def test_get_backend_falls_back_to_cpu_no_hailo() -> None:
    """get_backend() must return FasterWhisperBackend when Hailo not available."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub, hailo_available=False)

    backend = hw.get_backend()  # type: ignore[attr-defined]
    assert isinstance(backend, hw.FasterWhisperBackend)  # type: ignore[attr-defined]


def test_get_backend_force_cpu_skips_hailo(tmp_path: Path) -> None:
    """force_cpu=True must bypass Hailo even when HEF and Hailo are available."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub, hailo_available=True)

    hef = tmp_path / "whisper.hef"
    hef.write_bytes(b"fake")

    backend = hw.get_backend(hef_path=hef, force_cpu=True)  # type: ignore[attr-defined]
    assert isinstance(backend, hw.FasterWhisperBackend)  # type: ignore[attr-defined]


def test_get_backend_falls_back_when_hef_missing() -> None:
    """get_backend() must use CPU fallback when HEF path doesn't exist."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub, hailo_available=True)

    backend = hw.get_backend(hef_path=Path("/nonexistent.hef"))  # type: ignore[attr-defined]
    assert isinstance(backend, hw.FasterWhisperBackend)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# HailoWhisperBackend — fallback on NPU failure
# ---------------------------------------------------------------------------


def test_hailo_backend_falls_back_on_not_implemented(tmp_path: Path) -> None:
    """HailoWhisperBackend.transcribe() must fall back to CPU on NotImplementedError."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub, hailo_available=True)

    hef = tmp_path / "whisper.hef"
    hef.write_bytes(b"fake")

    backend: object = hw.HailoWhisperBackend(hef, npu_timeout_sec=1.0)  # type: ignore[attr-defined]

    # CPU fallback should transcribe the audio
    cpu_mock = AsyncMock(return_value="тест")
    backend._cpu_fallback = MagicMock()  # type: ignore[attr-defined]
    backend._cpu_fallback.transcribe = cpu_mock  # type: ignore[attr-defined]

    result = asyncio.run(backend.transcribe(b"fake_audio"))  # type: ignore[attr-defined]
    assert result == "тест"
    cpu_mock.assert_awaited_once_with(b"fake_audio")


def test_hailo_backend_falls_back_on_timeout(tmp_path: Path) -> None:
    """HailoWhisperBackend.transcribe() must fall back to CPU on asyncio.TimeoutError."""
    stub = _stub_faster_whisper()
    hw = _reload_hailo_whisper(faster_whisper_stub=stub, hailo_available=True)

    hef = tmp_path / "whisper.hef"
    hef.write_bytes(b"fake")

    backend: object = hw.HailoWhisperBackend(hef, npu_timeout_sec=0.001)  # type: ignore[attr-defined]

    # Make the Hailo path hang long enough to trigger timeout
    async def slow_transcribe() -> str:
        await asyncio.sleep(10)
        return "never"

    cpu_mock = AsyncMock(return_value="fallback_result")
    backend._cpu_fallback = MagicMock()  # type: ignore[attr-defined]
    backend._cpu_fallback.transcribe = cpu_mock  # type: ignore[attr-defined]

    with patch.object(
        backend,
        "_transcribe_hailo",
        side_effect=lambda _: (_ for _ in ()).throw(NotImplementedError),
    ):  # type: ignore[attr-defined]
        result = asyncio.run(backend.transcribe(b"fake_audio"))  # type: ignore[attr-defined]

    assert result == "fallback_result"


# ---------------------------------------------------------------------------
# NPUScheduler — WHISPER_WAITS strategy
# ---------------------------------------------------------------------------


def test_scheduler_whisper_waits_cv_sets_idle() -> None:
    """cv_frame() must clear cv_idle on entry and set it on exit."""
    from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy

    scheduler = NPUScheduler(NPUStrategy.WHISPER_WAITS)
    assert scheduler._cv_idle.is_set()

    async def _run() -> None:
        assert scheduler._cv_idle.is_set()
        async with scheduler.cv_frame():
            # During CV frame, idle event is cleared
            assert not scheduler._cv_idle.is_set()
        # After CV frame, idle event is set again
        assert scheduler._cv_idle.is_set()

    asyncio.run(_run())


def test_scheduler_whisper_waits_acquires_after_cv() -> None:
    """whisper_inference() must complete after CV frame releases NPU."""
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
            await asyncio.sleep(0.001)  # slight delay so CV starts first
            async with scheduler.whisper_inference():
                order.append("whisper")

        await asyncio.gather(cv_task(), whisper_task())

    asyncio.run(_run())
    assert order == ["cv_start", "cv_end", "whisper"]


# ---------------------------------------------------------------------------
# NPUScheduler — PREEMPT strategy
# ---------------------------------------------------------------------------


def test_scheduler_preempt_whisper_gets_npu_after_cv_yields() -> None:
    """With PREEMPT, Whisper should acquire NPU once CV yields the frame."""
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
    # whisper must appear after at least one cv frame
    assert "whisper" in order
    assert "cv" in order


# ---------------------------------------------------------------------------
# NPUScheduler — stats tracking
# ---------------------------------------------------------------------------


def test_scheduler_tracks_acquisitions() -> None:
    """NPUScheduler must increment whisper_acquisitions after each transcription."""
    from hub.edge.voice.scheduler import NPUScheduler, NPUStrategy

    scheduler = NPUScheduler(NPUStrategy.WHISPER_WAITS)

    async def _run() -> None:
        async with scheduler.whisper_inference():
            pass
        async with scheduler.whisper_inference():
            pass

    asyncio.run(_run())
    assert scheduler.stats().whisper_acquisitions == 2

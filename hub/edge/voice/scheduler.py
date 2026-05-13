"""NPU contention scheduler for Hailo-8 shared between CV cascade and Whisper.

Context: CV pipeline runs YOLO continuously at ~15 FPS (≈67 ms/frame), each frame
occupying Hailo NPU for ~10-30 ms.  Whisper encoder also needs NPU in bursts
(~100-150 ms) whenever a voice command is detected.

Three strategies are implemented for thesis benchmarking (§8 PROJECT.md):

  PREEMPT       Whisper requests interrupt CV at next frame boundary.  Lowest
                Whisper latency, but introduces variable CV frame drops.

  ROUND_ROBIN   NPU time-sliced: CV gets `cv_window_ms` ms per cycle, Whisper
                gets `whisper_window_ms` ms.  Predictable sharing; suitable when
                both workloads are equally latency-sensitive.

  WHISPER_WAITS CV has exclusive priority.  Whisper waits for the inter-frame gap
                (≈67 ms − inference time).  Zero CV FPS impact; Whisper latency
                increases proportionally to NPU utilisation.

Benchmark these strategies with:
    hub.edge.voice.pipeline --npu-strategy {preempt,round_robin,whisper_waits}

Production note: NPUScheduler is single-process.  If CV and Voice run in separate
Docker containers, coordinate via MQTT (publish "home/system/npu/lease" with the
token-holder container ID and a TTL).  The single-process version is used for the
diploma benchmark where both pipelines are co-located.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Histogram

    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

if PROM_AVAILABLE:
    _CONTENTION_COUNTER = Counter(
        "iot_hub_npu_contention_total",
        "Times Whisper had to wait for NPU",
        ["strategy"],
    )
    _WHISPER_WAIT_HIST = Histogram(
        "iot_hub_npu_whisper_wait_ms",
        "Milliseconds Whisper waited for NPU access",
        ["strategy"],
        buckets=[5, 20, 50, 100, 200, 500, 1000],
    )
else:
    _CONTENTION_COUNTER = None
    _WHISPER_WAIT_HIST = None


class NPUStrategy(enum.Enum):
    PREEMPT = "preempt"
    ROUND_ROBIN = "round_robin"
    WHISPER_WAITS = "whisper_waits"


@dataclass
class NPUStats:
    strategy: NPUStrategy
    cv_frames_yielded: int = 0
    whisper_contentions: int = 0
    whisper_wait_ms_total: float = 0.0
    whisper_acquisitions: int = 0

    @property
    def whisper_wait_ms_avg(self) -> float:
        if self.whisper_acquisitions == 0:
            return 0.0
        return self.whisper_wait_ms_total / self.whisper_acquisitions


class NPUScheduler:
    """Async scheduler coordinating Hailo NPU between CV cascade and Whisper.

    Usage (both pipelines must share the same NPUScheduler instance):

        scheduler = NPUScheduler(NPUStrategy.WHISPER_WAITS)

        # CV pipeline (runs every frame):
        async with scheduler.cv_frame():
            features = hailo_detector.detect(frame)

        # Voice pipeline (runs on each utterance):
        async with scheduler.whisper_inference():
            text = hailo_whisper.encode(audio)
    """

    def __init__(
        self,
        strategy: NPUStrategy = NPUStrategy.WHISPER_WAITS,
        cv_window_ms: float = 60.0,
        whisper_window_ms: float = 200.0,
    ) -> None:
        self.strategy = strategy
        self._cv_window_ms = cv_window_ms
        self._whisper_window_ms = whisper_window_ms
        self._stats = NPUStats(strategy=strategy)

        # Shared lock — serialises actual NPU access in all strategies
        self._npu_lock = asyncio.Lock()

        # PREEMPT: Whisper sets this event; CV checks it before next frame
        self._preempt_requested = asyncio.Event()

        # WHISPER_WAITS: set when CV is idle (between frames)
        self._cv_idle = asyncio.Event()
        self._cv_idle.set()

        # ROUND_ROBIN: tracks whose window is active
        self._rr_whisper_turn = asyncio.Event()
        self._rr_deadline: float = 0.0  # monotonic deadline for current window

    # ------------------------------------------------------------------
    # Public context managers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def cv_frame(self) -> AsyncIterator[None]:
        """Wrap each CV inference frame. Honours the active strategy."""
        if self.strategy == NPUStrategy.PREEMPT:
            async with self._cv_frame_preempt():
                yield
        elif self.strategy == NPUStrategy.ROUND_ROBIN:
            async with self._cv_frame_round_robin():
                yield
        else:  # WHISPER_WAITS
            async with self._cv_frame_whisper_waits():
                yield

    @asynccontextmanager
    async def whisper_inference(self) -> AsyncIterator[None]:
        """Wrap Whisper NPU encoder pass. Honours the active strategy."""
        t_start = time.monotonic()
        contended = False

        if self.strategy == NPUStrategy.PREEMPT:
            async with self._whisper_preempt() as _contended:
                contended = _contended
                yield
        elif self.strategy == NPUStrategy.ROUND_ROBIN:
            async with self._whisper_round_robin() as _contended:
                contended = _contended
                yield
        else:  # WHISPER_WAITS
            async with self._whisper_waits() as _contended:
                contended = _contended
                yield

        wait_ms = (time.monotonic() - t_start) * 1000
        self._stats.whisper_acquisitions += 1
        self._stats.whisper_wait_ms_total += wait_ms
        if contended:
            self._stats.whisper_contentions += 1
        if PROM_AVAILABLE and _CONTENTION_COUNTER and _WHISPER_WAIT_HIST:
            if contended:
                _CONTENTION_COUNTER.labels(strategy=self.strategy.value).inc()
            _WHISPER_WAIT_HIST.labels(strategy=self.strategy.value).observe(wait_ms)

    def stats(self) -> NPUStats:
        return self._stats

    # ------------------------------------------------------------------
    # PREEMPT internals
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _cv_frame_preempt(self) -> AsyncIterator[None]:
        if self._preempt_requested.is_set():
            # Whisper is waiting — skip this CV frame (don't acquire NPU)
            self._stats.cv_frames_yielded += 1
            logger.debug("CV frame yielded to Whisper (PREEMPT)")
            yield
            return
        async with self._npu_lock:
            yield

    @asynccontextmanager
    async def _whisper_preempt(self) -> AsyncIterator[bool]:
        self._preempt_requested.set()
        # Wait for CV to finish its current frame (lock becomes available)
        contended = self._npu_lock.locked()
        async with self._npu_lock:
            self._preempt_requested.clear()
            yield contended

    # ------------------------------------------------------------------
    # ROUND_ROBIN internals
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _cv_frame_round_robin(self) -> AsyncIterator[None]:
        now = time.monotonic()
        if self._rr_whisper_turn.is_set() and now < self._rr_deadline:
            # Whisper's window — CV yields
            self._stats.cv_frames_yielded += 1
            await asyncio.sleep(max(0.0, self._rr_deadline - now))
            yield
            return
        async with self._npu_lock:
            yield
        # After CV frame, check if we should hand over to Whisper
        if not self._rr_whisper_turn.is_set():
            self._rr_whisper_turn.clear()

    @asynccontextmanager
    async def _whisper_round_robin(self) -> AsyncIterator[bool]:
        now = time.monotonic()
        # Wait for CV's current window to expire
        contended = self._npu_lock.locked()
        if contended:
            remaining = self._rr_deadline - now
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._rr_whisper_turn.set()
        self._rr_deadline = time.monotonic() + self._whisper_window_ms / 1000.0
        async with self._npu_lock:
            yield contended
        self._rr_whisper_turn.clear()
        self._rr_deadline = time.monotonic() + self._cv_window_ms / 1000.0

    # ------------------------------------------------------------------
    # WHISPER_WAITS internals
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _cv_frame_whisper_waits(self) -> AsyncIterator[None]:
        self._cv_idle.clear()
        async with self._npu_lock:
            yield
        self._cv_idle.set()

    @asynccontextmanager
    async def _whisper_waits(self) -> AsyncIterator[bool]:
        # Wait for CV gap (cv_idle event)
        contended = not self._cv_idle.is_set()
        if contended:
            logger.debug("Whisper waiting for CV gap (WHISPER_WAITS)")
            await self._cv_idle.wait()
        async with self._npu_lock:
            yield contended

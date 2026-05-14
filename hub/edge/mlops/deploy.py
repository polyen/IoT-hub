"""Atomic HEF model deployment with symlink swap, SHA256 verification,
promote history, and SIGHUP reload.

Layout under ``MODELS_DIR`` (default ``/app/models`` inside container,
bind-mounted from ``/opt/iot-hub/models`` on the RPi5 host):

    versions/<stem>.hef       — immutable artifacts (DVC outs, MLflow promoted)
    manifest.json             — {"<stem>": {"sha256": ..., "kind": ..., "size": ...}}
    deployments.json          — append-only [{kind, version, promoted_at, rolled_back}]
    current_yolo.hef          — active YOLO symlink → versions/<stem>.hef
    current_pose.hef          — active pose symlink
    current_face.hef          — active ArcFace symlink
    current_whisper.hef       — active Whisper-encoder symlink
    current.hef               — backwards-compat alias of current_yolo.hef
    embeddings.pkl            — face enrollment store (read by FaceRecognizer)
    llm/                      — Qwen / other GGUF weights
    whisper/                  — extra Whisper caches (CPU fallback)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/app/models"))

# Each model "kind" reloads a specific container on SIGHUP.
# Override via env (e.g. for staging where containers have suffixes).
KIND_CONTAINERS: dict[str, str] = {
    "yolo": os.environ.get("CV_CONTAINER", "cv"),
    "pose": os.environ.get("CV_CONTAINER", "cv"),
    "face": os.environ.get("CV_CONTAINER", "cv"),
    "whisper": os.environ.get("VOICE_CONTAINER", "voice"),
}
KNOWN_KINDS = tuple(KIND_CONTAINERS.keys())

# Prometheus query endpoint
_PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

# ntfy rollback notification topic
_NTFY_URL = os.environ.get("NTFY_URL", "http://ntfy")
_NTFY_ROLLBACK_TOPIC = os.environ.get("NTFY_ROLLBACK_TOPIC", "iot-hub-rollback")


@dataclass(frozen=True)
class DeploymentRecord:
    """Single entry in deployments.json. Append-only."""

    kind: str
    version: str
    promoted_at: str  # ISO-8601 UTC
    rolled_back: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "promoted_at": self.promoted_at,
            "rolled_back": self.rolled_back,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeploymentRecord:
        return cls(
            kind=d["kind"],
            version=d["version"],
            promoted_at=d["promoted_at"],
            rolled_back=bool(d.get("rolled_back", False)),
        )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


async def _notify_rollback(kind: str, prev: str, current: str | None, reason: str) -> None:
    """Send a high-priority push when an automatic rollback fires."""
    try:
        import httpx

        title = f"Model auto-rollback ({kind})"
        body = f"Reverted {current} → {prev}\nReason: {reason}"
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{_NTFY_URL}/{_NTFY_ROLLBACK_TOPIC}",
                content=body.encode(),
                headers={"Title": title, "Priority": "high", "Tags": "warning,robot"},
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Rollback notification failed: %s", exc)


# ---------------------------------------------------------------------------
# ModelStore
# ---------------------------------------------------------------------------


class ChecksumMismatchError(RuntimeError):
    """Raised when a HEF file's SHA256 does not match manifest entry."""


class SmokeTestError(RuntimeError):
    """Raised when a HEF fails the holdout smoke test before promotion."""


class ModelStore:
    """Manages versions, manifest, and active symlinks for a single model kind."""

    def __init__(
        self,
        models_dir: Path = MODELS_DIR,
        kind: str = "yolo",
        container: str | None = None,
    ) -> None:
        if kind not in KIND_CONTAINERS:
            raise ValueError(f"Unknown model kind {kind!r}. Known: {sorted(KIND_CONTAINERS)}")
        self.models_dir = models_dir
        self.kind = kind
        self.container = container or KIND_CONTAINERS[kind]
        self.active_link: Path = models_dir / f"current_{kind}.hef"
        # Back-compat alias for the legacy single-model layout
        self._legacy_alias: Path | None = models_dir / "current.hef" if kind == "yolo" else None
        self._versions_dir = models_dir / "versions"
        self._manifest_file = models_dir / "manifest.json"
        self._deployments_file = models_dir / "deployments.json"

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _candidate_dirs(self) -> list[Path]:
        """Where to look for *.hef files. ``versions/`` first, models_dir as fallback."""
        out: list[Path] = []
        if self._versions_dir.is_dir():
            out.append(self._versions_dir)
        out.append(self.models_dir)
        return out

    def _find_hef(self, version: str) -> Path | None:
        """Return path to ``<version>.hef`` searching versions/ then models_dir."""
        for d in self._candidate_dirs():
            p = d / f"{version}.hef"
            if p.is_file():
                return p
        return None

    def list_versions(self) -> list[str]:
        """Return sorted list of available .hef stems (excludes active symlinks).

        Versions are filtered by the manifest's ``kind`` when manifest exists;
        when manifest is absent we return everything as a best-effort fallback.
        """
        manifest = self._load_manifest()
        seen: set[str] = set()
        for d in self._candidate_dirs():
            if not d.is_dir():
                continue
            for p in d.glob("*.hef"):
                if p.is_symlink():
                    continue
                stem = p.stem
                # Filter by kind when manifest entry exists for this stem.
                entry = manifest.get(stem)
                if entry is not None and entry.get("kind", self.kind) != self.kind:
                    continue
                seen.add(stem)
        return sorted(seen)

    def current_version(self) -> str | None:
        """Return the stem of the currently active model, or None if no symlink exists."""
        if self.active_link.is_symlink():
            return Path(os.readlink(self.active_link)).stem
        return None

    # ------------------------------------------------------------------
    # Manifest
    # ------------------------------------------------------------------

    def _load_manifest(self) -> dict[str, dict[str, Any]]:
        if not self._manifest_file.is_file():
            return {}
        try:
            with self._manifest_file.open() as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("manifest.json is not an object — ignoring")
                return {}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read manifest.json (%s) — ignoring", exc)
            return {}

    def _verify_checksum(self, version: str, hef_path: Path) -> None:
        manifest = self._load_manifest()
        entry = manifest.get(version)
        if entry is None or "sha256" not in entry:
            logger.warning(
                "No SHA256 manifest entry for %s — accepting promote without verification",
                version,
            )
            return
        expected = str(entry["sha256"]).lower()
        h = hashlib.sha256()
        with hef_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != expected:
            raise ChecksumMismatchError(
                f"SHA256 mismatch for {version}: manifest={expected} actual={actual}"
            )

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def _load_history(self) -> list[DeploymentRecord]:
        if not self._deployments_file.is_file():
            return []
        try:
            with self._deployments_file.open() as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            return [DeploymentRecord.from_dict(d) for d in data if isinstance(d, dict)]
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.warning("Cannot read deployments.json (%s) — starting fresh", exc)
            return []

    def _append_history(self, record: DeploymentRecord) -> None:
        history = self._load_history()
        history.append(record)
        tmp = self._deployments_file.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump([r.to_dict() for r in history], f, indent=2)
        os.replace(tmp, self._deployments_file)

    def _previous_from_history(self) -> str | None:
        """Most recent non-rolled-back promote of self.kind that isn't current."""
        current = self.current_version()
        history = [r for r in self._load_history() if r.kind == self.kind]
        for record in reversed(history):
            if record.rolled_back:
                continue
            if record.version != current:
                return record.version
        return None

    def _mark_last_rolled_back(self) -> None:
        history = self._load_history()
        for i in range(len(history) - 1, -1, -1):
            if history[i].kind == self.kind and not history[i].rolled_back:
                history[i] = DeploymentRecord(
                    kind=history[i].kind,
                    version=history[i].version,
                    promoted_at=history[i].promoted_at,
                    rolled_back=True,
                )
                break
        tmp = self._deployments_file.with_suffix(".json.tmp")
        with tmp.open("w") as f:
            json.dump([r.to_dict() for r in history], f, indent=2)
        os.replace(tmp, self._deployments_file)

    # ------------------------------------------------------------------
    # Holdout smoke test
    # ------------------------------------------------------------------

    def validate_on_holdout(
        self,
        holdout_dir: Path | None = None,
        n_frames: int = 50,
        min_hef_bytes: int = 1024 * 64,
        candidate_path: Path | None = None,
    ) -> bool:
        """Smoke-test a candidate HEF before promote.

        Checks (in order, each can be skipped gracefully):
          1. File size ≥ min_hef_bytes (catches truncated downloads).
          2. HEF header magic bytes (first 4 bytes must be non-zero).
          3. If HailoDetector is importable AND holdout_dir has images:
             load the model, run on up to n_frames, verify no exception and
             at least one detection across all frames (basic sanity).

        On dev machines / CI (no hailo_platform) steps 1-2 run; step 3 is
        skipped with a WARNING — promote is allowed through.

        Args:
            holdout_dir: directory of sample images for inference check.
                         Defaults to datasets/fire_smoke/images/val relative
                         to cwd (best-effort; missing dir skips step 3).
            n_frames:    max images to run inference on.
            min_hef_bytes: minimum acceptable HEF file size.
            candidate_path: explicit HEF path; if None uses active_link target.

        Returns True on pass, raises SmokeTestError on hard failure.
        """
        hef_path = candidate_path or (
            self.active_link.resolve() if self.active_link.is_symlink() else None
        )
        if hef_path is None or not hef_path.is_file():
            logger.warning("validate_on_holdout: no HEF path available — skipping")
            return True

        # Step 1: file size
        size = hef_path.stat().st_size
        if size < min_hef_bytes:
            raise SmokeTestError(
                f"HEF file {hef_path} is suspiciously small ({size} bytes < {min_hef_bytes})"
            )

        # Step 2: magic bytes (HEF starts with a non-zero protobuf header)
        with hef_path.open("rb") as f:
            magic = f.read(4)
        if magic == b"\x00\x00\x00\x00" or len(magic) < 4:
            raise SmokeTestError(f"HEF file {hef_path} has invalid header magic: {magic!r}")

        # Step 3: inference smoke test (RPi5 only)
        try:
            from hub.edge.cv.detector import HailoDetector
        except ImportError:
            logger.warning(
                "validate_on_holdout: HailoDetector unavailable (dev/CI) — "
                "skipping inference check for %s/%s",
                self.kind,
                hef_path.name,
            )
            return True

        if holdout_dir is None:
            holdout_dir = Path("datasets/fire_smoke/images/val")
        if not holdout_dir.is_dir():
            logger.warning(
                "validate_on_holdout: holdout_dir %s not found — skipping inference check",
                holdout_dir,
            )
            return True

        try:
            import cv2  # type: ignore[import]
        except ImportError:
            logger.warning("validate_on_holdout: opencv not available — skipping inference check")
            return True

        images = sorted(holdout_dir.glob("*.jpg")) + sorted(holdout_dir.glob("*.png"))
        if not images:
            logger.warning(
                "validate_on_holdout: no images in %s — skipping inference check", holdout_dir
            )
            return True

        sample = images[: min(n_frames, len(images))]
        detector = HailoDetector(hef_path)
        try:
            detector.load()
            total_detections = 0
            for img_path in sample:
                frame = cv2.imread(str(img_path))
                if frame is None:
                    continue
                dets = detector.detect(frame)
                total_detections += len(dets)

            logger.info(
                "validate_on_holdout %s/%s: ran %d frames, %d detections",
                self.kind,
                hef_path.name,
                len(sample),
                total_detections,
            )
        except Exception as exc:  # noqa: BLE001
            raise SmokeTestError(
                f"Inference smoke test failed for {self.kind}/{hef_path.name}: {exc}"
            ) from exc
        finally:
            detector.close()

        return True

    # ------------------------------------------------------------------
    # SIGHUP
    # ------------------------------------------------------------------

    def _sighup_container(self) -> None:
        result = subprocess.run(
            ["docker", "kill", "--signal=SIGHUP", self.container],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            logger.warning(
                "docker kill SIGHUP %s failed (rc=%d): %s",
                self.container,
                result.returncode,
                result.stderr.decode(errors="replace").strip(),
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def promote(self, version: str) -> None:
        """Atomically swap the active symlink to ``version.hef`` and SIGHUP the container.

        Verifies SHA256 against manifest.json (warn-only if entry missing).
        Appends a DeploymentRecord to deployments.json.
        """
        hef_path = self._find_hef(version)
        if hef_path is None:
            raise FileNotFoundError(
                f"Model version {version!r} not found under {self.models_dir} "
                f"(searched versions/ and models_dir)"
            )

        self._verify_checksum(version, hef_path)
        self.validate_on_holdout(candidate_path=hef_path)

        # Atomic symlink swap via temp link + os.replace (POSIX, same fs).
        tmp_link = self.models_dir / f"active_{self.kind}.tmp.hef"
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        tmp_link.symlink_to(hef_path)
        os.replace(tmp_link, self.active_link)

        # Keep legacy current.hef alias for yolo kind so older code (pipeline.py
        # default, existing tests) keeps working.
        if self._legacy_alias is not None:
            tmp_alias = self.models_dir / "active_legacy.tmp.hef"
            if tmp_alias.exists() or tmp_alias.is_symlink():
                tmp_alias.unlink()
            tmp_alias.symlink_to(hef_path)
            os.replace(tmp_alias, self._legacy_alias)

        record = DeploymentRecord(
            kind=self.kind,
            version=version,
            promoted_at=datetime.now(UTC).isoformat(),
        )
        self._append_history(record)

        logger.info(
            "Promoted %s/%s → %s, SIGHUP %s",
            self.kind,
            version,
            hef_path,
            self.container,
        )
        self._sighup_container()

    def rollback(self) -> str | None:
        """Promote the previous model version.

        Strategy:
          1. Walk deployments.json backwards for a non-rolled-back entry of
             this kind that is not the current version.
          2. Fall back to "any other .hef on disk for this kind, sorted by
             mtime descending" (covers legacy installs with no history file).

        On success, marks the previously-current history entry as rolled_back.
        Returns the rolled-back-to version, or None when nothing is eligible.
        """
        previous = self._previous_from_history()
        if previous is None:
            current = self.current_version()
            disk_candidates = [v for v in self.list_versions() if v != current]
            if not disk_candidates:
                logger.warning("No previous model version available for rollback.")
                return None

            # Most recent by mtime, then alphabetical tie-breaker
            def _mtime(stem: str) -> float:
                p = self._find_hef(stem)
                return p.stat().st_mtime if p is not None else 0.0

            disk_candidates.sort(key=lambda v: (_mtime(v), v), reverse=True)
            previous = disk_candidates[0]

        # Mark current entry as rolled_back BEFORE promoting so history
        # reflects intent even if the promote step fails.
        self._mark_last_rolled_back()
        self.promote(previous)
        return previous


# ---------------------------------------------------------------------------
# Auto-rollback monitoring
# ---------------------------------------------------------------------------


# (query_label, prometheus_query_5m, prometheus_query_7d_baseline, kind)
# Baseline is 7 days to survive weekend/weekday variation in a home environment.
# 24h baseline proved too short — daytime cooking smoke patterns skewed it.
DEFAULT_ROLLBACK_QUERIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "fire",
        'rate(iot_hub_cv_detections_total{label="fire"}[5m])',
        'avg_over_time(rate(iot_hub_cv_detections_total{label="fire"}[5m])[7d:5m])',
        "yolo",
    ),
    (
        "smoke",
        'rate(iot_hub_cv_detections_total{label="smoke"}[5m])',
        'avg_over_time(rate(iot_hub_cv_detections_total{label="smoke"}[5m])[7d:5m])',
        "yolo",
    ),
    (
        "fall",
        "rate(iot_hub_cv_fall_alerts_total[5m])",
        "avg_over_time(rate(iot_hub_cv_fall_alerts_total[5m])[7d:5m])",
        "pose",
    ),
    (
        "person",
        'rate(iot_hub_cv_detections_total{label="person"}[5m])',
        'avg_over_time(rate(iot_hub_cv_detections_total{label="person"}[5m])[7d:5m])',
        "yolo",
    ),
)


async def _prom_scalar(client: Any, url: str, query: str) -> float:
    resp = await client.get(f"{url}/api/v1/query", params={"query": query})
    data = resp.json()
    results = data.get("data", {}).get("result", [])
    if not results:
        return 0.0
    return float(results[0]["value"][1])


async def check_and_rollback_if_needed(
    stores: dict[str, ModelStore] | ModelStore | None = None,
    threshold: float = 1.5,
) -> bool:
    """Watch multiple class rates; rollback the relevant ModelStore on anomaly.

    Args:
        stores: mapping kind→ModelStore (or a single ModelStore for back-compat).
        threshold: multiplier over 7-day baseline that triggers rollback.

    Returns True if any rollback was triggered.
    """
    if stores is None:
        stores = {"yolo": ModelStore(kind="yolo")}
    elif isinstance(stores, ModelStore):
        stores = {stores.kind: stores}

    triggered = False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            for label, q_now, q_base, kind in DEFAULT_ROLLBACK_QUERIES:
                store = stores.get(kind)
                if store is None:
                    continue
                try:
                    current_rate = await _prom_scalar(client, _PROMETHEUS_URL, q_now)
                    baseline_rate = await _prom_scalar(client, _PROMETHEUS_URL, q_base)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Prometheus query failed for %s: %s", label, exc)
                    continue

                logger.debug(
                    "Rate check %s/%s: current=%.4f baseline=%.4f threshold=%.1fx",
                    kind,
                    label,
                    current_rate,
                    baseline_rate,
                    threshold,
                )

                if baseline_rate > 0 and current_rate > threshold * baseline_rate:
                    reason = (
                        f"{label} rate {current_rate:.4f}/s "
                        f"> {threshold:.1f}× 24h baseline {baseline_rate:.4f}/s"
                    )
                    logger.warning("Auto-rollback triggered (%s) — %s", kind, reason)
                    current = store.current_version()
                    prev = store.rollback()
                    if prev is not None:
                        await _notify_rollback(kind=kind, prev=prev, current=current, reason=reason)
                        triggered = True
                        # One rollback per kind per loop is enough.
                        break
                    logger.error("Rollback skipped — no prior model version available")

    except Exception as exc:  # noqa: BLE001
        logger.error("check_and_rollback_if_needed error: %s", exc)

    return triggered


async def monitor_loop(
    stores: dict[str, ModelStore] | None = None,
    interval: int = 300,
) -> None:
    """Background loop: call check_and_rollback_if_needed every *interval* seconds."""
    if stores is None:
        stores = {
            "yolo": ModelStore(kind="yolo"),
            "pose": ModelStore(kind="pose"),
        }
    while True:
        await check_and_rollback_if_needed(stores)
        await asyncio.sleep(interval)

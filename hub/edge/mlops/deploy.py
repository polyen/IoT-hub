"""Atomic HEF model deployment with symlink swap and SIGHUP reload."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/app/models"))
CV_CONTAINER = os.environ.get("CV_CONTAINER", "cv")

# Prometheus query endpoint
_PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

# ntfy URL for rollback notifications (T4.5: "Rollback notify у Telegram").
# We publish to a generic ntfy topic; the cloud Telegram bot subscribes to it.
_NTFY_URL = os.environ.get("NTFY_URL", "http://ntfy")
_NTFY_ROLLBACK_TOPIC = os.environ.get("NTFY_ROLLBACK_TOPIC", "iot-hub-rollback")


async def _notify_rollback(prev: str, current: str | None, reason: str) -> None:
    """Send a high-priority push when an automatic rollback fires."""
    try:
        import httpx

        title = "Model auto-rollback"
        body = f"Reverted {current} → {prev}\nReason: {reason}"
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(
                f"{_NTFY_URL}/{_NTFY_ROLLBACK_TOPIC}",
                content=body.encode(),
                headers={"Title": title, "Priority": "high", "Tags": "warning,robot"},
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("Rollback notification failed: %s", exc)


class ModelStore:
    """Manages HEF model versions and the current active symlink."""

    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        self.models_dir = models_dir
        self.active_link: Path = models_dir / "current.hef"

    def list_versions(self) -> list[str]:
        """Return sorted list of available .hef versions (excludes the active symlink)."""
        versions: list[str] = []
        for p in self.models_dir.glob("*.hef"):
            # Skip the symlink itself
            if p.is_symlink() and p.name == self.active_link.name:
                continue
            if not p.is_symlink():
                versions.append(p.stem)
        return sorted(versions)

    def current_version(self) -> str | None:
        """Return the stem of the currently active model, or None if no symlink exists."""
        if self.active_link.is_symlink():
            target = Path(os.readlink(self.active_link))
            return target.stem
        return None

    def promote(self, version: str) -> None:
        """Atomically swap the active symlink to *version*.hef and SIGHUP the CV container.

        Raises FileNotFoundError if ``version.hef`` does not exist in models_dir.
        """
        hef_path = self.models_dir / f"{version}.hef"
        if not hef_path.exists():
            raise FileNotFoundError(f"Model version '{version}' not found: {hef_path}")

        # Atomic symlink swap using a temporary link + os.replace
        tmp_link = self.models_dir / "active.tmp.hef"
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()

        # Create temp symlink → target
        tmp_link.symlink_to(hef_path)
        # os.replace is atomic on POSIX for same filesystem
        os.replace(tmp_link, self.active_link)

        logger.info("Promoted %s, SIGHUP sent to %s", version, CV_CONTAINER)

        subprocess.run(
            ["docker", "kill", "--signal=SIGHUP", CV_CONTAINER],
            check=False,
            capture_output=True,
        )

    def rollback(self) -> str | None:
        """Promote the previous model version (the one that is not current).

        Returns the rolled-back version name, or None if no other version is available.
        """
        versions = self.list_versions()
        current = self.current_version()

        # Find all versions that differ from current
        candidates = [v for v in versions if v != current]
        if not candidates:
            logger.warning("No previous model version to roll back to.")
            return None

        # Pick the last one alphabetically (highest version number / most recent name)
        previous = candidates[-1]
        self.promote(previous)
        return previous


async def check_and_rollback_if_needed(
    store: ModelStore,
    threshold: float = 1.5,
) -> bool:
    """Query Prometheus and trigger rollback if fire detection rate is anomalously high.

    Returns True if a rollback was triggered, False otherwise.
    """
    try:
        import httpx

        query_5m = 'rate(iot_hub_cv_detections_total{class_name="fire"}[5m])'
        query_24h = (
            'avg_over_time(rate(iot_hub_cv_detections_total{class_name="fire"}[5m])[24h:5m])'
        )

        async with httpx.AsyncClient(timeout=10) as client:
            r_current = await client.get(
                f"{_PROMETHEUS_URL}/api/v1/query",
                params={"query": query_5m},
            )
            r_baseline = await client.get(
                f"{_PROMETHEUS_URL}/api/v1/query",
                params={"query": query_24h},
            )

        def _scalar(resp: Any) -> float:
            data = resp.json()
            results = data.get("data", {}).get("result", [])
            if not results:
                return 0.0
            return float(results[0]["value"][1])

        current_rate = _scalar(r_current)
        baseline_rate = _scalar(r_baseline)

        logger.debug(
            "Fire rate check: current=%.4f baseline=%.4f threshold=%.1f×",
            current_rate,
            baseline_rate,
            threshold,
        )

        if baseline_rate > 0 and current_rate > threshold * baseline_rate:
            reason = (
                f"fire detection rate {current_rate:.4f}/s "
                f"> {threshold:.1f}× 24h baseline {baseline_rate:.4f}/s"
            )
            logger.warning("Auto-rollback triggered — %s", reason)
            current = store.current_version()
            prev = store.rollback()
            if prev is not None:
                await _notify_rollback(prev=prev, current=current, reason=reason)
                return True
            logger.error("Rollback skipped — no prior model version available")

    except Exception as exc:  # noqa: BLE001
        logger.error("check_and_rollback_if_needed error: %s", exc)

    return False


async def monitor_loop(store: ModelStore, interval: int = 300) -> None:
    """Background loop: call check_and_rollback_if_needed every *interval* seconds."""
    while True:
        await check_and_rollback_if_needed(store)
        await asyncio.sleep(interval)

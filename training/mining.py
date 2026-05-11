"""Hard-negative miner: builds YOLO fine-tune datasets from FP feedback frames."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hub.backend.models import FeedbackEvent

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


async def fetch_fp_feedback(
    session: AsyncSession,
    since: datetime,
) -> list[FeedbackEvent]:
    """Return FP-labelled FeedbackEvents created after *since*, ordered by tag."""
    result = await session.execute(
        select(FeedbackEvent)
        .where(FeedbackEvent.user_label == "fp")
        .where(FeedbackEvent.ts >= since)
        .order_by(FeedbackEvent.tag)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Balancing
# ---------------------------------------------------------------------------


def balance_by_tag(
    events: list[FeedbackEvent],
    max_per_tag: int = 100,
) -> list[FeedbackEvent]:
    """Limit to *max_per_tag* events per tag value ('untagged' when tag is None)."""
    buckets: dict[str, list[FeedbackEvent]] = defaultdict(list)
    for ev in events:
        key = ev.tag if ev.tag is not None else "untagged"
        buckets[key].append(ev)

    balanced: list[FeedbackEvent] = []
    for tag_events in buckets.values():
        balanced.extend(tag_events[:max_per_tag])
    return balanced


# ---------------------------------------------------------------------------
# Versioned dataset directory
# ---------------------------------------------------------------------------


def find_next_version(base_dir: Path) -> int:
    """Scan *base_dir*/feedback_v*/ subdirs and return next version number."""
    existing: list[int] = []
    if base_dir.exists():
        for d in base_dir.iterdir():
            if d.is_dir() and d.name.startswith("feedback_v"):
                try:
                    existing.append(int(d.name[len("feedback_v") :]))
                except ValueError:
                    pass
    return (max(existing) + 1) if existing else 1


# ---------------------------------------------------------------------------
# Frame I/O
# ---------------------------------------------------------------------------


def copy_frame(frame_ref: str, dest_dir: Path) -> bool:
    """Copy frame from T0 path to *dest_dir*. Returns False if missing."""
    src = Path(frame_ref)
    if not src.exists():
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest_dir / src.name)
    return True


def write_yolo_label(image_path: Path, label_dir: Path) -> None:
    """Write a placeholder YOLO label file (class 0, full-frame bbox).

    Actual labels would be provided by human annotators.
    Format: <class_id> <cx> <cy> <w> <h>  (all normalised 0-1)
    """
    label_dir.mkdir(parents=True, exist_ok=True)
    label_path = label_dir / (image_path.stem + ".txt")
    label_path.write_text("0 0.5 0.5 1.0 1.0\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_mining(
    db_url: str,
    days: int = 30,
    max_per_tag: int = 100,
    out_base: Path = Path("datasets/fire_smoke"),
) -> None:
    """Orchestrate hard-negative mining pipeline."""
    # T0 guard — warn and skip frame copy if not available
    t0_available = True
    try:
        from hub.edge.storage.t0 import assert_t0_available

        assert_t0_available()
    except ImportError:
        logger.warning("hub.edge.storage.t0 not importable — frame copy skipped")
        t0_available = False
    except Exception as exc:  # T0StorageError or RuntimeError
        logger.warning("T0 storage not available (%s) — frame copy skipped", exc)
        t0_available = False

    engine = create_async_engine(db_url, echo=False)
    async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    since = datetime.now(UTC) - timedelta(days=days)

    async with async_session() as session:
        events = await fetch_fp_feedback(session, since)

    await engine.dispose()

    events = balance_by_tag(events, max_per_tag=max_per_tag)

    version = find_next_version(out_base)
    version_dir = out_base / f"feedback_v{version}"
    images_dir = version_dir / "images"
    labels_dir = version_dir / "labels"

    mined = 0
    for ev in events:
        if ev.frame_blob_ref is None:
            continue

        if not t0_available:
            continue
        copied = copy_frame(ev.frame_blob_ref, images_dir)

        if copied:
            frame_name = Path(ev.frame_blob_ref).name
            image_path = images_dir / frame_name
            write_yolo_label(image_path, labels_dir)
            mined += 1

    print(f"Mined {mined} frames into {version_dir}/")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hard-negative miner for YOLO fine-tuning.")
    parser.add_argument("--db-url", required=True, help="Async SQLAlchemy database URL")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Look back N days for FP feedback (default: 30)",
    )
    parser.add_argument(
        "--max-per-tag",
        type=int,
        default=100,
        help="Max frames per tag bucket (default: 100)",
    )
    parser.add_argument(
        "--out-base",
        type=Path,
        default=Path("datasets/fire_smoke"),
        help="Output base directory (default: datasets/fire_smoke)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    args = _parse_args(argv)
    asyncio.run(
        run_mining(
            db_url=args.db_url,
            days=args.days,
            max_per_tag=args.max_per_tag,
            out_base=args.out_base,
        )
    )


if __name__ == "__main__":
    main()

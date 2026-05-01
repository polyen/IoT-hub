"""Integration tests for DB models — round-trip insert/query + vector similarity.

Requires a running PostgreSQL with TimescaleDB + pgvector.
Run via: make up-infra && pytest tests/integration/test_models.py

Uses the DATABASE_URL env var (defaults to localhost:5432/iothub).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hub.backend.db import AsyncSessionLocal, engine
from hub.backend.models import AgentAudit, Base, Event, EventEmbedding, FeedbackEvent

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module", autouse=True)
async def create_tables() -> None:
    """Create all tables before the test module runs; drop after."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        await conn.run_sync(Base.metadata.create_all)
    yield  # type: ignore[misc]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    async with AsyncSessionLocal() as s:
        yield s
        await s.rollback()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _random_embedding(dim: int = 768) -> list[float]:
    vec = np.random.rand(dim).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec.tolist()


def _make_event(**kwargs: object) -> Event:
    return Event(
        id=uuid.uuid4(),
        timestamp=datetime.now(UTC),
        type="test_event",
        tier=0,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_event_insert_query(session: AsyncSession) -> None:
    event = _make_event(room="living_room", payload={"confidence": 0.95})
    session.add(event)
    await session.commit()

    result = await session.get(Event, event.id)
    assert result is not None
    assert result.room == "living_room"
    assert result.payload == {"confidence": 0.95}
    assert result.tier == 0


async def test_agent_audit_insert_query(session: AsyncSession) -> None:
    audit = AgentAudit(
        id=uuid.uuid4(),
        timestamp=datetime.now(UTC),
        intent_text="turn on the lights",
        tool="switch_control",
        action_class="AUTO",
        executed=True,
        latency_ms=42,
        llm_version="qwen3-4b",
    )
    session.add(audit)
    await session.commit()

    result = await session.get(AgentAudit, audit.id)
    assert result is not None
    assert result.action_class == "AUTO"
    assert result.executed is True
    assert result.latency_ms == 42


async def test_feedback_event_insert_query(session: AsyncSession) -> None:
    event = _make_event(type="fall_detected", tier=2)
    session.add(event)
    await session.flush()

    feedback = FeedbackEvent(
        id=uuid.uuid4(),
        alert_id=event.id,
        user_label="false_positive",
        tag="cv_fall",
        ts=datetime.now(UTC),
    )
    session.add(feedback)
    await session.commit()

    result = await session.get(FeedbackEvent, feedback.id)
    assert result is not None
    assert result.user_label == "false_positive"


async def test_event_embedding_topk(session: AsyncSession) -> None:
    """Insert 5 events with embeddings; query top-2 by cosine similarity."""
    query_vec = _random_embedding()

    for _ in range(5):
        ev = _make_event(type="embedding_test", tier=1)
        session.add(ev)
        await session.flush()

        emb = EventEmbedding(
            id=uuid.uuid4(),
            event_id=ev.id,
            embedding=_random_embedding(),
        )
        session.add(emb)

    await session.commit()

    # Cosine similarity top-K via pgvector <=> operator (cosine distance, lower = closer)
    stmt = (
        select(EventEmbedding)
        .order_by(EventEmbedding.embedding.cosine_distance(query_vec))  # type: ignore[attr-defined]
        .limit(2)
    )
    results = (await session.execute(stmt)).scalars().all()
    assert len(results) == 2

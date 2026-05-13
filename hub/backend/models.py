"""SQLAlchemy 2.0 ORM models for IoT Hub backend."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Event(Base):
    """Sensor / CV / system event stream.

    Partitioned as a TimescaleDB hypertable on ``timestamp``.
    Only T0/T1 tier data is replicated to cloud.
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False, index=True
    )
    room: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    # tier: 0=public-aggregate, 1=non-sensitive, 2=sensitive, 3=private
    tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(64))
    user_consent_cloud: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    embedding: Mapped[EventEmbedding | None] = relationship(
        "EventEmbedding",
        back_populates="event",
        primaryjoin="Event.id == foreign(EventEmbedding.event_id)",
        uselist=False,
        viewonly=True,
    )
    feedback: Mapped[list[FeedbackEvent]] = relationship(
        "FeedbackEvent",
        back_populates="alert",
        primaryjoin="Event.id == foreign(FeedbackEvent.alert_id)",
        viewonly=True,
    )


class AgentAudit(Base):
    """Audit log for every LLM agent action decision.

    Retention: 90 days on edge; 30 days on cloud (non-sensitive fields only).
    """

    __tablename__ = "agent_audit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    intent_text: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str | None] = mapped_column(String(128))
    # action_class: AUTO | CONFIRM | DENY
    action_class: Mapped[str] = mapped_column(String(16), nullable=False)
    executed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # confirmation: 'user_approved' | 'user_rejected' | None
    confirmation: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    llm_version: Mapped[str | None] = mapped_column(String(64))


class FeedbackEvent(Base):
    """Human feedback labels on CV alerts — used for active learning loop (T4.x)."""

    __tablename__ = "feedback_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No DB-level FK to events: TimescaleDB hypertables require the partition
    # column in every unique constraint, making a standalone FK on id impossible.
    alert_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_label: Mapped[str] = mapped_column(String(64), nullable=False)
    # frame_blob_ref: path/S3 key to the frame — stored on edge, never in cloud
    frame_blob_ref: Mapped[str | None] = mapped_column(Text)
    tag: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    alert: Mapped[Event] = relationship(
        "Event",
        back_populates="feedback",
        primaryjoin="FeedbackEvent.alert_id == Event.id",
        foreign_keys="[FeedbackEvent.alert_id]",
        viewonly=True,
    )


class EventEmbedding(Base):
    """768-dim text/event embeddings for semantic search (pgvector).

    Stored edge-only (tier 2). Never replicated to cloud.
    """

    __tablename__ = "event_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # No DB-level FK — same TimescaleDB hypertable limitation as FeedbackEvent.
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    embedding: Mapped[Any] = mapped_column(Vector(768), nullable=False)

    event: Mapped[Event] = relationship(
        "Event",
        back_populates="embedding",
        primaryjoin="EventEmbedding.event_id == Event.id",
        foreign_keys="[EventEmbedding.event_id]",
        viewonly=True,
    )


# ---------------------------------------------------------------------------
# Raw SQL helpers used by migrations
# ---------------------------------------------------------------------------
TIMESCALE_HYPERTABLE_SQL = text(
    "SELECT create_hypertable('events', 'timestamp', if_not_exists => TRUE);"
)

PGVECTOR_EXTENSION_SQL = text("CREATE EXTENSION IF NOT EXISTS vector;")
TIMESCALE_EXTENSION_SQL = text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")

"""SQLAlchemy 2.0 ORM models for IoT Hub backend."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
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
    T1+T2 tier data is replicated to cloud (WHERE tier IN (1,2) AND user_consent_cloud).
    """

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False, index=True
    )
    room: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    # tier: 0=T0 raw/sensitive (edge-only), 1=T1 personal events (cloud opt-in),
    #        2=T2 aggregated (cloud OK), 3=T3 operational (cloud OK)
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


class FloorPlan(Base):
    """User-defined floor plan canvas."""

    __tablename__ = "floor_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    floor: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    width: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    height: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    background_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    rooms: Mapped[list[Room]] = relationship(
        "Room", back_populates="floor_plan", cascade="all, delete-orphan"
    )


class Room(Base):
    """Room polygon inside a floor plan (coords normalised [0..1])."""

    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    floor_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("floor_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Stable ASCII MQTT identity (home/{slug}/..., cv:detections:{slug}).
    # Generated from `name` on create — see hub.backend.slug.slugify_room.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")
    polygon: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    color: Mapped[str | None] = mapped_column(String(16))
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    floor_plan: Mapped[FloorPlan] = relationship("FloorPlan", back_populates="rooms")
    placements: Mapped[list[DevicePlacement]] = relationship(
        "DevicePlacement", back_populates="room", cascade="all, delete-orphan"
    )


class DevicePlacement(Base):
    """Device icon placed on a room's canvas (coords normalised [0..1])."""

    __tablename__ = "device_placements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    y: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    label: Mapped[str | None] = mapped_column(String(128))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    room: Mapped[Room] = relationship("Room", back_populates="placements")


class ConfirmRequest(Base):
    """Pending CONFIRM-class action request waiting for user decision."""

    __tablename__ = "confirm_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tool: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    intent_text: Mapped[str] = mapped_column(Text, nullable=False)
    confirm_message: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_origin: Mapped[str | None] = mapped_column(String(64))
    # state: pending | approved | rejected | timeout | executed
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Raw SQL helpers used by migrations
# ---------------------------------------------------------------------------
TIMESCALE_HYPERTABLE_SQL = text(
    "SELECT create_hypertable('events', 'timestamp', if_not_exists => TRUE);"
)

PGVECTOR_EXTENSION_SQL = text("CREATE EXTENSION IF NOT EXISTS vector;")
TIMESCALE_EXTENSION_SQL = text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")

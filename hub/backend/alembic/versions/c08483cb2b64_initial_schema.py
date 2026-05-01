"""initial schema — events, agent_audit, feedback_events, event_embeddings

Revision ID: c08483cb2b64
Revises:
Create Date: 2026-05-01 13:55:22.456085

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "c08483cb2b64"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial schema with extensions, tables and hypertable."""
    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    op.create_table(
        "events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("room", sa.String(64), nullable=True),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("model_version", sa.String(64), nullable=True),
    )
    op.create_index("ix_events_timestamp", "events", ["timestamp"])

    # TimescaleDB hypertable — partition by timestamp
    op.execute("SELECT create_hypertable('events', 'timestamp', if_not_exists => TRUE);")

    # ------------------------------------------------------------------
    # agent_audit
    # ------------------------------------------------------------------
    op.create_table(
        "agent_audit",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intent_text", sa.Text, nullable=False),
        sa.Column("tool", sa.String(128), nullable=True),
        sa.Column("action_class", sa.String(16), nullable=False),
        sa.Column("executed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("confirmation", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("llm_version", sa.String(64), nullable=True),
    )
    op.create_index("ix_agent_audit_timestamp", "agent_audit", ["timestamp"])

    # ------------------------------------------------------------------
    # feedback_events
    # ------------------------------------------------------------------
    op.create_table(
        "feedback_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "alert_id",
            UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_label", sa.String(64), nullable=False),
        sa.Column("frame_blob_ref", sa.Text, nullable=True),
        sa.Column("tag", sa.String(64), nullable=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_feedback_events_ts", "feedback_events", ["ts"])

    # ------------------------------------------------------------------
    # event_embeddings (pgvector 768-dim)
    # ------------------------------------------------------------------
    op.create_table(
        "event_embeddings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("embedding", Vector(768), nullable=False),
    )

    # HNSW index for approximate nearest-neighbour search (cosine distance)
    op.execute("CREATE INDEX ON event_embeddings USING hnsw (embedding vector_cosine_ops);")


def downgrade() -> None:
    """Drop all tables created in this revision."""
    op.drop_table("event_embeddings")
    op.drop_table("feedback_events")
    op.drop_table("agent_audit")
    op.drop_table("events")

"""web_ui_tables: floor_plans, rooms, device_placements, confirm_requests

Revision ID: e3f4a5b6c7d8
Revises: d1a2b3c4d5e6
Create Date: 2026-05-13 17:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e3f4a5b6c7d8"
down_revision = "d1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "floor_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("floor", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("width", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("height", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("background_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "rooms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("floor_plan_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("polygon", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["floor_plan_id"], ["floor_plans.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "device_placements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("x", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("y", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "confirm_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tool", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("intent_text", sa.Text(), nullable=False),
        sa.Column("confirm_message", sa.Text(), nullable=False),
        sa.Column("schedule_origin", sa.String(64), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("confirm_requests")
    op.drop_table("device_placements")
    op.drop_table("rooms")
    op.drop_table("floor_plans")

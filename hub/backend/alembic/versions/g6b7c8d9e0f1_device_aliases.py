"""device_aliases: add voice-control columns to device_placements and rooms

Revision ID: g6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-05-28 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "g6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- rooms: add aliases for voice room resolution ---
    op.add_column(
        "rooms",
        sa.Column(
            "aliases",
            postgresql.JSONB(),
            nullable=False,
            server_default="'[]'::jsonb",
        ),
    )
    op.create_index(
        "ix_rooms_aliases_gin",
        "rooms",
        ["aliases"],
        postgresql_using="gin",
    )

    # --- device_placements: add voice-control columns ---
    op.add_column(
        "device_placements",
        sa.Column(
            "aliases",
            postgresql.JSONB(),
            nullable=False,
            server_default="'[]'::jsonb",
        ),
    )
    op.add_column(
        "device_placements",
        sa.Column(
            "controllable",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "device_placements",
        sa.Column(
            "actions",
            postgresql.JSONB(),
            nullable=False,
            server_default="'[]'::jsonb",
        ),
    )
    op.create_index(
        "ix_device_placements_aliases_gin",
        "device_placements",
        ["aliases"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_device_placements_aliases_gin", table_name="device_placements")
    op.drop_column("device_placements", "actions")
    op.drop_column("device_placements", "controllable")
    op.drop_column("device_placements", "aliases")

    op.drop_index("ix_rooms_aliases_gin", table_name="rooms")
    op.drop_column("rooms", "aliases")

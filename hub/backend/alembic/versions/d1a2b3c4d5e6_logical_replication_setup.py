"""Logical replication setup for edge→VPS sync.

Revision ID: d1a2b3c4d5e6
Revises: c08483cb2b64
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1a2b3c4d5e6"
down_revision = "c08483cb2b64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("user_consent_cloud", sa.Boolean(), nullable=False, server_default="false"),
    )

    # Note: wal_level=logical must be set manually (requires PG restart):
    #   ALTER SYSTEM SET wal_level = 'logical';
    #   SELECT pg_reload_conf();
    op.execute(
        "CREATE PUBLICATION events_pub FOR TABLE events "
        "WHERE (tier IN (1, 2) AND user_consent_cloud = true)"
    )


def downgrade() -> None:
    op.execute("DROP PUBLICATION IF EXISTS events_pub")
    op.drop_column("events", "user_consent_cloud")

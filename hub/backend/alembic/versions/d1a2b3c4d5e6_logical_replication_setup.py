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

    # CREATE PUBLICATION requires wal_level=logical.
    # On a fresh container wal_level=replica — wrap in DO block so the migration
    # succeeds and the column is added even when the publication can't be created.
    # To activate replication later:
    #   ALTER SYSTEM SET wal_level = 'logical';  -- then restart postgres
    op.execute(
        """
        DO $$ BEGIN
            CREATE PUBLICATION events_pub
            FOR TABLE events
            WHERE (tier IN (1, 2) AND user_consent_cloud = true);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Skipping publication setup (requires wal_level=logical): %', SQLERRM;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP PUBLICATION IF EXISTS events_pub")
    op.drop_column("events", "user_consent_cloud")

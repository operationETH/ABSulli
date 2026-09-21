"""add rss feed metadata

Revision ID: 0006_add_rss_feed_metadata
Revises: 0005_add_library_archive_state
Create Date: 2026-09-18
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_add_rss_feed_metadata"
down_revision = "0005_add_library_archive_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notification_events",
        sa.Column("library_id", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "notification_events",
        sa.Column("context_json", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("notification_events", "context_json")
    op.drop_column("notification_events", "library_id")

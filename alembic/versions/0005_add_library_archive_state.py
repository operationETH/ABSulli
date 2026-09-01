"""add library archive state

Revision ID: 0005_add_library_archive_state
Revises: 0004_add_notification_deliveries
Create Date: 2026-08-31
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_add_library_archive_state"
down_revision = "0004_add_notification_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "libraries",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "libraries",
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("libraries", "archived_at")
    op.drop_column("libraries", "is_active")

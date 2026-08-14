"""add notification deliveries

Revision ID: 0004_add_notification_deliveries
Revises: 0003_drop_media_item_cover_url
Create Date: 2026-08-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_add_notification_deliveries"
down_revision = "0003_drop_media_item_cover_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("agent", sa.String(length=64), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["notification_events.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_deliveries_event_id", "notification_deliveries", ["event_id"], unique=False)
    op.create_index("ix_notification_deliveries_agent", "notification_deliveries", ["agent"], unique=False)
    op.create_index("ix_notification_deliveries_delivered", "notification_deliveries", ["delivered"], unique=False)
    op.create_index("ix_notification_deliveries_attempted_at", "notification_deliveries", ["attempted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_attempted_at", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_delivered", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_agent", table_name="notification_deliveries")
    op.drop_index("ix_notification_deliveries_event_id", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")

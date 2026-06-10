"""drop media item cover_url

Revision ID: 0003_drop_media_item_cover_url
Revises: 0002_drop_listening_history_raw_json
Create Date: 2026-06-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_drop_media_item_cover_url"
down_revision = "0002_drop_listening_history_raw_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_items") as batch_op:
        batch_op.drop_column("cover_url")


def downgrade() -> None:
    with op.batch_alter_table("media_items") as batch_op:
        batch_op.add_column(sa.Column("cover_url", sa.Text(), nullable=False, server_default=""))

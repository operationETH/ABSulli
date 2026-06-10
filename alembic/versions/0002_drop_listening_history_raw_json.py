"""drop listening history raw_json

Revision ID: 0002_drop_listening_history_raw_json
Revises: 0001_initial_schema
Create Date: 2026-06-09
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_drop_listening_history_raw_json"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("listening_history") as batch_op:
        batch_op.drop_column("raw_json")


def downgrade() -> None:
    with op.batch_alter_table("listening_history") as batch_op:
        batch_op.add_column(sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"))

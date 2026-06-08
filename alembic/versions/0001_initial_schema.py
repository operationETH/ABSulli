"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-08
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "abs_users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("abs_user_id", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("abs_user_id"),
    )
    op.create_index("ix_abs_users_abs_user_id", "abs_users", ["abs_user_id"], unique=True)
    op.create_index("ix_abs_users_username", "abs_users", ["username"], unique=False)

    op.create_table(
        "libraries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("abs_library_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("abs_library_id"),
    )
    op.create_index("ix_libraries_abs_library_id", "libraries", ["abs_library_id"], unique=True)

    op.create_table(
        "media_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("abs_item_id", sa.String(length=128), nullable=False),
        sa.Column("library_id", sa.String(length=128), nullable=False),
        sa.Column("library_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("author", sa.String(length=512), nullable=False),
        sa.Column("author_id", sa.String(length=128), nullable=False),
        sa.Column("narrator", sa.String(length=512), nullable=False),
        sa.Column("series", sa.String(length=512), nullable=False),
        sa.Column("year", sa.String(length=64), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("size_bytes", sa.Float(), nullable=False),
        sa.Column("cover_url", sa.Text(), nullable=False),
        sa.Column("added_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("abs_item_id"),
    )
    op.create_index("ix_media_items_abs_item_id", "media_items", ["abs_item_id"], unique=True)
    op.create_index("ix_media_items_author_id", "media_items", ["author_id"], unique=False)
    op.create_index("ix_media_items_library_id", "media_items", ["library_id"], unique=False)
    op.create_index("ix_media_items_title", "media_items", ["title"], unique=False)

    op.create_table(
        "activity_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_key", sa.String(length=255), nullable=False),
        sa.Column("abs_user_id", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("abs_item_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("author", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("library_id", sa.String(length=128), nullable=False),
        sa.Column("library_name", sa.String(length=255), nullable=False),
        sa.Column("device", sa.String(length=255), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("client", sa.String(length=255), nullable=False),
        sa.Column("ip_address", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("current_time", sa.Float(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("time_listening", sa.Float(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_key"),
    )
    op.create_index("ix_activity_sessions_abs_item_id", "activity_sessions", ["abs_item_id"], unique=False)
    op.create_index("ix_activity_sessions_abs_user_id", "activity_sessions", ["abs_user_id"], unique=False)
    op.create_index("ix_activity_sessions_library_id", "activity_sessions", ["library_id"], unique=False)
    op.create_index("ix_activity_sessions_session_key", "activity_sessions", ["session_key"], unique=True)

    op.create_table(
        "listening_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("abs_session_id", sa.String(length=255), nullable=False),
        sa.Column("abs_user_id", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("abs_item_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("author", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("library_id", sa.String(length=128), nullable=False),
        sa.Column("library_name", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("current_time", sa.Float(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("device", sa.String(length=255), nullable=False),
        sa.Column("device_name", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("client", sa.String(length=255), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("abs_session_id", name="uq_abs_session_id"),
    )
    op.create_index("ix_listening_history_abs_item_id", "listening_history", ["abs_item_id"], unique=False)
    op.create_index("ix_listening_history_abs_session_id", "listening_history", ["abs_session_id"], unique=False)
    op.create_index("ix_listening_history_abs_user_id", "listening_history", ["abs_user_id"], unique=False)
    op.create_index("ix_listening_history_library_id", "listening_history", ["library_id"], unique=False)

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("client_key", sa.String(length=128), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_key"),
    )
    op.create_index("ix_login_attempts_client_key", "login_attempts", ["client_key"], unique=True)

    op.create_table(
        "login_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("client_key", sa.String(length=128), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=128), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_logs_client_key", "login_logs", ["client_key"], unique=False)
    op.create_index("ix_login_logs_created_at", "login_logs", ["created_at"], unique=False)
    op.create_index("ix_login_logs_success", "login_logs", ["success"], unique=False)
    op.create_index("ix_login_logs_username", "login_logs", ["username"], unique=False)

    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notification_events_event_type", "notification_events", ["event_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_notification_events_event_type", table_name="notification_events")
    op.drop_table("notification_events")

    op.drop_index("ix_login_logs_username", table_name="login_logs")
    op.drop_index("ix_login_logs_success", table_name="login_logs")
    op.drop_index("ix_login_logs_created_at", table_name="login_logs")
    op.drop_index("ix_login_logs_client_key", table_name="login_logs")
    op.drop_table("login_logs")

    op.drop_index("ix_login_attempts_client_key", table_name="login_attempts")
    op.drop_table("login_attempts")

    op.drop_index("ix_listening_history_library_id", table_name="listening_history")
    op.drop_index("ix_listening_history_abs_user_id", table_name="listening_history")
    op.drop_index("ix_listening_history_abs_session_id", table_name="listening_history")
    op.drop_index("ix_listening_history_abs_item_id", table_name="listening_history")
    op.drop_table("listening_history")

    op.drop_index("ix_activity_sessions_session_key", table_name="activity_sessions")
    op.drop_index("ix_activity_sessions_library_id", table_name="activity_sessions")
    op.drop_index("ix_activity_sessions_abs_user_id", table_name="activity_sessions")
    op.drop_index("ix_activity_sessions_abs_item_id", table_name="activity_sessions")
    op.drop_table("activity_sessions")

    op.drop_index("ix_media_items_title", table_name="media_items")
    op.drop_index("ix_media_items_library_id", table_name="media_items")
    op.drop_index("ix_media_items_author_id", table_name="media_items")
    op.drop_index("ix_media_items_abs_item_id", table_name="media_items")
    op.drop_table("media_items")

    op.drop_index("ix_libraries_abs_library_id", table_name="libraries")
    op.drop_table("libraries")

    op.drop_index("ix_abs_users_username", table_name="abs_users")
    op.drop_index("ix_abs_users_abs_user_id", table_name="abs_users")
    op.drop_table("abs_users")

    op.drop_table("settings")

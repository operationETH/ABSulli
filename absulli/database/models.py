from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from absulli.core.time import utcnow
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AbsUser(Base):
    __tablename__ = "abs_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    abs_user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    abs_library_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="Unknown")
    media_type: Mapped[str] = mapped_column(String(64), default="unknown")
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MediaItem(Base):
    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    abs_item_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    library_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    library_name: Mapped[str] = mapped_column(String(255), default="")
    media_type: Mapped[str] = mapped_column(String(64), default="unknown")
    title: Mapped[str] = mapped_column(String(512), index=True)
    author: Mapped[str] = mapped_column(String(512), default="")
    author_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    narrator: Mapped[str] = mapped_column(String(512), default="")
    series: Mapped[str] = mapped_column(String(512), default="")
    year: Mapped[str] = mapped_column(String(64), default="")
    duration: Mapped[float] = mapped_column(Float, default=0)
    size_bytes: Mapped[float] = mapped_column(Float, default=0)
    added_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActivitySession(Base):
    __tablename__ = "activity_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    abs_user_id: Mapped[str] = mapped_column(String(128), index=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    abs_item_id: Mapped[str] = mapped_column(String(128), index=True, default="")
    title: Mapped[str] = mapped_column(String(512), default="Unknown")
    author: Mapped[str] = mapped_column(String(512), default="")
    media_type: Mapped[str] = mapped_column(String(64), default="unknown")
    library_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    library_name: Mapped[str] = mapped_column(String(255), default="")
    device: Mapped[str] = mapped_column(String(255), default="")
    device_name: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    client: Mapped[str] = mapped_column(String(255), default="")
    ip_address: Mapped[str] = mapped_column(String(128), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    current_time: Mapped[float] = mapped_column(Float, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    time_listening: Mapped[float] = mapped_column(Float, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ListeningHistory(Base):
    __tablename__ = "listening_history"
    __table_args__ = (UniqueConstraint("abs_session_id", name="uq_abs_session_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    abs_session_id: Mapped[str] = mapped_column(String(255), index=True)
    abs_user_id: Mapped[str] = mapped_column(String(128), index=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    abs_item_id: Mapped[str] = mapped_column(String(128), index=True, default="")
    title: Mapped[str] = mapped_column(String(512), default="Unknown")
    author: Mapped[str] = mapped_column(String(512), default="")
    media_type: Mapped[str] = mapped_column(String(64), default="unknown")
    library_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    library_name: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0)
    current_time: Mapped[float] = mapped_column(Float, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0)
    device: Mapped[str] = mapped_column(String(255), default="")
    device_name: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    client: Mapped[str] = mapped_column(String(255), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    first_failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)




class LoginLog(Base):
    __tablename__ = "login_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_key: Mapped[str] = mapped_column(String(128), index=True)
    username: Mapped[str] = mapped_column(String(255), default="", index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reason: Mapped[str] = mapped_column(String(64), default="")
    ip_address: Mapped[str] = mapped_column(String(128), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    host: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class NotificationEvent(Base):
    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("notification_events.id"), index=True)
    agent: Mapped[str] = mapped_column(String(64), index=True)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

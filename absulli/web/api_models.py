from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StatusResponse(BaseModel):
    status: str
    time: str
    setup_required: bool
    abs_reachable: bool | None
    abs_last_success_at: str
    abs_last_failure_at: str
    active_sessions: int
    history_rows: int
    users: int
    libraries: int
    recent_items: int


class ActivityRow(BaseModel):
    session_key: str
    abs_item_id: str
    username: str
    title: str
    author: str
    media_type: str
    library_id: str
    library_name: str
    current_time: float
    duration: float
    time_listening: float
    progress: float
    device: str
    device_name: str
    model: str
    client: str
    started_at: datetime
    updated_at: datetime | None
    last_seen_at: datetime


class HistoryRow(BaseModel):
    session_key: str
    abs_item_id: str
    username: str
    title: str
    author: str
    media_type: str
    library_id: str
    library_name: str
    duration_seconds: float
    current_time: float
    progress: float
    client: str
    model: str
    started_at: datetime | None
    updated_at: datetime | None
    imported_at: datetime


class UserRow(BaseModel):
    id: str
    username: str
    display_name: str
    is_active: bool


class LibraryRow(BaseModel):
    id: str
    name: str
    media_type: str
    item_count: int
    updated_at: datetime


class RevokeResponse(BaseModel):
    status: str
    revoked_sessions: bool

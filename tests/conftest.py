import os
import tempfile

os.environ.setdefault("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
os.environ.setdefault("ABSULLI_DATA_DIR", tempfile.mkdtemp(prefix="absulli-tests-"))


import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import close_all_sessions

from absulli.database.session import engine as app_engine
from absulli.database.session import init_db

init_db()

_test_engines: set[Engine] = set()


@event.listens_for(Engine, "engine_connect")
def _track_test_engine(connection):
    if connection.engine is not app_engine:
        _test_engines.add(connection.engine)


@pytest.fixture(autouse=True)
def dispose_test_engines():
    yield
    close_all_sessions()
    while _test_engines:
        _test_engines.pop().dispose()

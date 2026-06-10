import os
import tempfile

os.environ.setdefault("ABSULLI_SECRET_KEY", "test-secret-key-that-is-long-enough-32")
os.environ.setdefault("ABSULLI_DATA_DIR", tempfile.mkdtemp(prefix="absulli-tests-"))


from absulli.database.session import init_db

init_db()

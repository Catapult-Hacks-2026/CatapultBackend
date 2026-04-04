import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core import cache


@pytest.fixture()
def tmp_db(tmp_path):
    """Provide a temporary SQLite database with the full schema initialized."""
    db_path = tmp_path / "test.db"
    with patch("app.core.database.DB_PATH", db_path):
        from app.core.database import init_db, get_db

        init_db()
        conn = get_db()
        yield conn
        conn.close()


@pytest.fixture(autouse=True)
def reset_memory_cache():
    """Clear the in-memory cache fallback between tests."""
    cache._memory_store.clear()
    cache._memory_sets.clear()
    old_pool = cache._redis_pool
    cache._redis_pool = None
    yield
    cache._memory_store.clear()
    cache._memory_sets.clear()
    cache._redis_pool = old_pool

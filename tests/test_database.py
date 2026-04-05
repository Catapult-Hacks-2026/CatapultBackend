import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "negotiations.db"
        database._redis_client = None

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        database._redis_client = None

    def test_get_db_configures_sqlite_connection(self) -> None:
        with patch.object(database, "DB_PATH", self.db_path):
            conn = database.get_db()
            try:
                self.assertIs(conn.row_factory, sqlite3.Row)
                foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(foreign_keys, 1)
        self.assertEqual(journal_mode.lower(), "wal")

    def test_init_db_creates_tables_and_adds_missing_columns(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            CREATE TABLE galileo_enterprises (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE galileo_companies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE galileo_events (
                id TEXT PRIMARY KEY,
                enterprise_id TEXT NOT NULL,
                name TEXT NOT NULL,
                service TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE galileo_agents (
                id TEXT PRIMARY KEY,
                enterprise_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                company_id TEXT NOT NULL,
                company_name TEXT,
                segment TEXT,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                lifecycle_status TEXT NOT NULL DEFAULT 'INITIALIZING'
            )
            """
        )
        conn.commit()
        conn.close()

        with (
            patch.object(database, "DB_PATH", self.db_path),
            patch("app.memory.behavioral_store.migrate_call_history_from_chroma"),
            patch("app.services.rag.migrate_from_chroma"),
            patch("app.services.market_data.seed_market_data"),
            patch("app.services.market_data.sync_market_data_to_redis"),
        ):
            database.init_db()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(galileo_agents)").fetchall()
            }
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("product_category", columns)
        self.assertIn("research_brief", columns)
        self.assertIn("messages", tables)
        self.assertIn("latest_quotes", tables)
        self.assertIn("session_locks", tables)
        self.assertNotIn("negotiations", tables)

    def test_get_redis_builds_and_caches_client(self) -> None:
        sentinel = object()
        with patch("redis.Redis.from_url", return_value=sentinel) as from_url:
            first = database.get_redis()
            second = database.get_redis()

        self.assertIs(first, sentinel)
        self.assertIs(second, sentinel)
        from_url.assert_called_once_with(
            database.settings.redis_url,
            decode_responses=True,
        )


if __name__ == "__main__":
    unittest.main()

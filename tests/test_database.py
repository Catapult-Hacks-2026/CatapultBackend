import json
import sqlite3
import uuid
from unittest.mock import patch

import pytest

from app.core.database import init_db, get_db


class TestInitDb:
    """Verify that init_db creates all expected tables and columns."""

    def test_creates_all_tables(self, tmp_db):
        tables = {
            row[0]
            for row in tmp_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "negotiations",
            "messages",
            "call_sessions",
            "campaigns",
            "campaign_jobs",
            "quote_events",
            "latest_quotes",
            "session_locks",
            "memory_candidates",
            "validated_features",
        }
        assert expected.issubset(tables)

    def test_wal_mode_enabled(self, tmp_db):
        mode = tmp_db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_enabled(self, tmp_db):
        fk = tmp_db.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_idempotent(self, tmp_path):
        """Calling init_db twice should not raise."""
        db_path = tmp_path / "test_idem.db"
        with patch("app.core.database.DB_PATH", db_path):
            init_db()
            init_db()


class TestNegotiationsTable:
    """CRUD operations on the negotiations table."""

    def _insert_negotiation(self, conn, neg_id=None, vendor="TestVendor", config=None):
        neg_id = neg_id or str(uuid.uuid4())
        config = config or json.dumps({"target_price": 100})
        conn.execute(
            "INSERT INTO negotiations (id, vendor_name, config) VALUES (?, ?, ?)",
            (neg_id, vendor, config),
        )
        conn.commit()
        return neg_id

    def test_insert_and_select(self, tmp_db):
        neg_id = self._insert_negotiation(tmp_db)
        row = tmp_db.execute(
            "SELECT * FROM negotiations WHERE id = ?", (neg_id,)
        ).fetchone()
        assert row["vendor_name"] == "TestVendor"
        assert row["status"] == "pending"
        assert row["strategy"] == "balanced"
        assert row["round_number"] == 0
        assert row["max_rounds"] == 10

    def test_default_columns(self, tmp_db):
        neg_id = self._insert_negotiation(tmp_db)
        row = tmp_db.execute(
            "SELECT product_category, worker_status FROM negotiations WHERE id = ?",
            (neg_id,),
        ).fetchone()
        assert row["product_category"] == "general"
        assert row["worker_status"] == "idle"

    def test_update_status(self, tmp_db):
        neg_id = self._insert_negotiation(tmp_db)
        tmp_db.execute(
            "UPDATE negotiations SET status = 'active' WHERE id = ?", (neg_id,)
        )
        tmp_db.commit()
        row = tmp_db.execute(
            "SELECT status FROM negotiations WHERE id = ?", (neg_id,)
        ).fetchone()
        assert row["status"] == "active"

    def test_json_config_roundtrip(self, tmp_db):
        config = {"target_price": 50, "max_price": 80, "items": ["widget"]}
        neg_id = self._insert_negotiation(tmp_db, config=json.dumps(config))
        row = tmp_db.execute(
            "SELECT config FROM negotiations WHERE id = ?", (neg_id,)
        ).fetchone()
        assert json.loads(row["config"]) == config

    def test_duplicate_id_raises(self, tmp_db):
        neg_id = self._insert_negotiation(tmp_db)
        with pytest.raises(sqlite3.IntegrityError):
            self._insert_negotiation(tmp_db, neg_id=neg_id)


class TestMessagesTable:

    def _setup_negotiation(self, conn):
        neg_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO negotiations (id, vendor_name, config) VALUES (?, ?, ?)",
            (neg_id, "V", "{}"),
        )
        conn.commit()
        return neg_id

    def test_insert_and_select(self, tmp_db):
        neg_id = self._setup_negotiation(tmp_db)
        tmp_db.execute(
            "INSERT INTO messages (negotiation_id, role, content) VALUES (?, ?, ?)",
            (neg_id, "agent", "Hello vendor"),
        )
        tmp_db.commit()
        row = tmp_db.execute(
            "SELECT * FROM messages WHERE negotiation_id = ?", (neg_id,)
        ).fetchone()
        assert row["role"] == "agent"
        assert row["content"] == "Hello vendor"

    def test_autoincrement_id(self, tmp_db):
        neg_id = self._setup_negotiation(tmp_db)
        tmp_db.execute(
            "INSERT INTO messages (negotiation_id, role, content) VALUES (?, ?, ?)",
            (neg_id, "agent", "msg1"),
        )
        tmp_db.execute(
            "INSERT INTO messages (negotiation_id, role, content) VALUES (?, ?, ?)",
            (neg_id, "vendor", "msg2"),
        )
        tmp_db.commit()
        rows = tmp_db.execute(
            "SELECT id FROM messages WHERE negotiation_id = ? ORDER BY id", (neg_id,)
        ).fetchall()
        assert rows[1]["id"] > rows[0]["id"]

    def test_foreign_key_constraint(self, tmp_db):
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.execute(
                "INSERT INTO messages (negotiation_id, role, content) VALUES (?, ?, ?)",
                ("nonexistent", "agent", "boom"),
            )
            tmp_db.commit()


class TestCampaignsAndJobs:

    def _insert_campaign(self, conn, campaign_id=None):
        campaign_id = campaign_id or str(uuid.uuid4())
        conn.execute(
            "INSERT INTO campaigns (id, name) VALUES (?, ?)",
            (campaign_id, "Test Campaign"),
        )
        conn.commit()
        return campaign_id

    def test_campaign_defaults(self, tmp_db):
        cid = self._insert_campaign(tmp_db)
        row = tmp_db.execute(
            "SELECT * FROM campaigns WHERE id = ?", (cid,)
        ).fetchone()
        assert row["status"] == "pending"
        assert row["max_concurrent_workers"] == 5
        assert row["completed_jobs"] == 0

    def test_campaign_job_linked(self, tmp_db):
        cid = self._insert_campaign(tmp_db)
        job_id = str(uuid.uuid4())
        tmp_db.execute(
            "INSERT INTO campaign_jobs (id, campaign_id, vendor_name, target_config) "
            "VALUES (?, ?, ?, ?)",
            (job_id, cid, "Vendor A", "{}"),
        )
        tmp_db.commit()
        row = tmp_db.execute(
            "SELECT * FROM campaign_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row["campaign_id"] == cid
        assert row["status"] == "queued"


class TestQuoteTables:

    def test_quote_event_insert(self, tmp_db):
        qid = str(uuid.uuid4())
        tmp_db.execute(
            "INSERT INTO quote_events (id, vendor_name, unit_price) VALUES (?, ?, ?)",
            (qid, "SupplierX", 42.5),
        )
        tmp_db.commit()
        row = tmp_db.execute(
            "SELECT * FROM quote_events WHERE id = ?", (qid,)
        ).fetchone()
        assert row["unit_price"] == 42.5
        assert row["source"] == "call"

    def test_latest_quotes_upsert(self, tmp_db):
        tmp_db.execute(
            "INSERT OR REPLACE INTO latest_quotes (vendor_name, product_category, unit_price) "
            "VALUES (?, ?, ?)",
            ("SupplierX", "widgets", 100.0),
        )
        tmp_db.execute(
            "INSERT OR REPLACE INTO latest_quotes (vendor_name, product_category, unit_price) "
            "VALUES (?, ?, ?)",
            ("SupplierX", "widgets", 90.0),
        )
        tmp_db.commit()
        rows = tmp_db.execute(
            "SELECT * FROM latest_quotes WHERE vendor_name = 'SupplierX'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["unit_price"] == 90.0


class TestValidatedFeatures:

    def test_unique_constraint(self, tmp_db):
        fid = str(uuid.uuid4())
        tmp_db.execute(
            "INSERT INTO validated_features (id, vendor_name, product_category, feature_type, feature_value) "
            "VALUES (?, ?, ?, ?, ?)",
            (fid, "V1", "cat1", "discount_tendency", '{"avg": 0.1}'),
        )
        tmp_db.commit()
        with pytest.raises(sqlite3.IntegrityError):
            tmp_db.execute(
                "INSERT INTO validated_features (id, vendor_name, product_category, feature_type, feature_value) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "V1", "cat1", "discount_tendency", '{"avg": 0.2}'),
            )
            tmp_db.commit()

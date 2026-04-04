import sqlite3
from pathlib import Path

import redis

from app.core.config import get_settings

settings = get_settings()

DB_PATH = Path("data/negotiations.db")
_redis_client: redis.Redis | None = None


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS negotiations (
            id TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            product_category TEXT NOT NULL DEFAULT 'general',
            status TEXT NOT NULL DEFAULT 'pending',
            strategy TEXT NOT NULL DEFAULT 'balanced',
            config JSON NOT NULL,
            current_offer JSON,
            research_brief TEXT,
            utility_score REAL,
            round_number INTEGER DEFAULT 0,
            max_rounds INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negotiation_id TEXT NOT NULL REFERENCES negotiations(id),
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            structured_data JSON,
            utility_score REAL,
            rag_context JSON,
            guardrail_log JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS call_sessions (
            id TEXT PRIMARY KEY,
            negotiation_id TEXT NOT NULL REFERENCES negotiations(id),
            twilio_call_sid TEXT,
            status TEXT DEFAULT 'active',
            transcript TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS quote_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negotiation_id TEXT NOT NULL REFERENCES negotiations(id),
            vendor_name TEXT NOT NULL,
            product_category TEXT NOT NULL DEFAULT 'general',
            extracted_facts JSON,
            offer JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS latest_quotes (
            negotiation_id TEXT PRIMARY KEY REFERENCES negotiations(id),
            vendor_name TEXT NOT NULL,
            product_category TEXT NOT NULL DEFAULT 'general',
            extracted_facts JSON,
            offer JSON,
            utility_score REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS session_locks (
            lock_key TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS historic_pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hotel TEXT NOT NULL,
            location TEXT NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            price_per_night REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS past_negotiations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hotel TEXT NOT NULL,
            location TEXT NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            starting_price REAL NOT NULL,
            negotiation_price REAL NOT NULL,
            proposed_price REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    _ensure_column(conn, "negotiations", "product_category", "TEXT NOT NULL DEFAULT 'general'")
    _ensure_column(conn, "negotiations", "research_brief", "TEXT")
    conn.commit()
    conn.close()

    # One-time migration: move legacy ChromaDB data into Redis
    from app.memory.behavioral_store import migrate_call_history_from_chroma
    from app.services.rag import migrate_from_chroma
    chroma_dir = settings.chroma_persist_dir
    migrate_from_chroma(chroma_dir)
    migrate_call_history_from_chroma(chroma_dir)


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
    return _redis_client

import sqlite3
from pathlib import Path

import chromadb

from app.core.config import get_settings

settings = get_settings()

DB_PATH = Path("data/negotiations.db")
_chroma_client = None


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

        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            max_concurrent_workers INTEGER DEFAULT 5,
            max_budget REAL,
            total_target_jobs INTEGER DEFAULT 0,
            completed_jobs INTEGER DEFAULT 0,
            failed_jobs INTEGER DEFAULT 0,
            market_signals TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS campaign_jobs (
            id TEXT PRIMARY KEY,
            campaign_id TEXT REFERENCES campaigns(id),
            negotiation_id TEXT REFERENCES negotiations(id),
            vendor_name TEXT NOT NULL,
            vendor_phone TEXT,
            product_category TEXT DEFAULT 'general',
            target_config TEXT NOT NULL,
            priority_score REAL DEFAULT 0.0,
            deadline TIMESTAMP,
            status TEXT DEFAULT 'queued',
            retry_count INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 2,
            deferred_until TIMESTAMP,
            failure_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS quote_events (
            id TEXT PRIMARY KEY,
            negotiation_id TEXT REFERENCES negotiations(id),
            vendor_name TEXT NOT NULL,
            product_category TEXT,
            unit_price REAL NOT NULL,
            shipping_cost REAL,
            payment_terms_days INTEGER,
            delivery_days INTEGER,
            confidence_score REAL DEFAULT 1.0,
            restrictions TEXT,
            source TEXT DEFAULT 'call',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS latest_quotes (
            vendor_name TEXT NOT NULL,
            product_category TEXT NOT NULL,
            unit_price REAL NOT NULL,
            shipping_cost REAL,
            payment_terms_days INTEGER,
            delivery_days INTEGER,
            confidence_score REAL DEFAULT 1.0,
            restrictions TEXT,
            quote_timestamp TIMESTAMP,
            negotiation_id TEXT,
            PRIMARY KEY (vendor_name, product_category)
        );

        CREATE TABLE IF NOT EXISTS session_locks (
            lock_key TEXT PRIMARY KEY,
            negotiation_id TEXT REFERENCES negotiations(id),
            acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            worker_id TEXT
        );

        CREATE TABLE IF NOT EXISTS memory_candidates (
            id TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            product_category TEXT,
            pattern_type TEXT,
            pattern_description TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            evidence_count INTEGER DEFAULT 1,
            validated INTEGER DEFAULT 0,
            source_negotiation_ids TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS validated_features (
            id TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            product_category TEXT,
            feature_type TEXT NOT NULL,
            feature_value TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(vendor_name, product_category, feature_type)
        );
        """
    )
    _ensure_column(conn, "negotiations", "product_category", "TEXT NOT NULL DEFAULT 'general'")
    _ensure_column(conn, "negotiations", "research_brief", "TEXT")
    _ensure_column(conn, "negotiations", "campaign_id", "TEXT REFERENCES campaigns(id)")
    _ensure_column(conn, "negotiations", "thread_id", "TEXT")
    _ensure_column(conn, "negotiations", "worker_status", "TEXT DEFAULT 'idle'")
    _ensure_column(conn, "negotiations", "manager_reached", "INTEGER DEFAULT 0")
    _ensure_column(conn, "negotiations", "callback_requested", "INTEGER DEFAULT 0")
    _ensure_column(conn, "negotiations", "final_outcome", "TEXT")
    _ensure_column(conn, "negotiations", "call_started_at", "TIMESTAMP")
    _ensure_column(conn, "negotiations", "call_ended_at", "TIMESTAMP")
    _ensure_column(conn, "messages", "extracted_facts", "TEXT")
    conn.commit()
    conn.close()


def get_chroma() -> chromadb.ClientAPI:
    global _chroma_client
    if _chroma_client is None:
        persist_dir = Path(settings.chroma_persist_dir)
        persist_dir.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(persist_dir))
    return _chroma_client


def get_vendor_collection() -> chromadb.Collection:
    client = get_chroma()
    return client.get_or_create_collection(
        name="vendor_history",
        metadata={"hnsw:space": "cosine"},
    )

import sqlite3
import chromadb
from pathlib import Path
from app.core.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# SQLite — negotiation session state
# ---------------------------------------------------------------------------

DB_PATH = Path("data/negotiations.db")


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS negotiations (
            id TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            strategy TEXT NOT NULL DEFAULT 'balanced',
            config JSON NOT NULL,       -- buyer weights, ceilings, targets
            current_offer JSON,         -- latest vendor offer parsed
            utility_score REAL,
            round_number INTEGER DEFAULT 0,
            max_rounds INTEGER DEFAULT 10,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negotiation_id TEXT NOT NULL REFERENCES negotiations(id),
            role TEXT NOT NULL,          -- 'vendor' | 'agent' | 'system'
            content TEXT NOT NULL,
            structured_data JSON,       -- parsed offer or counter-offer
            utility_score REAL,
            rag_context JSON,           -- which memories were retrieved
            guardrail_log JSON,         -- validation pass/fail details
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
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# ChromaDB — vendor history vector store
# ---------------------------------------------------------------------------

_chroma_client = None


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

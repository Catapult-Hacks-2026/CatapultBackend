import json
import logging
import sqlite3
from pathlib import Path

import redis

from app.core.config import get_settings
from app.core.negotiation_store import (
    NEGOTIATION_ENTERPRISE_ID,
    NEGOTIATION_EVENT_ID,
    ensure_negotiation_scaffold,
    ensure_vendor_company,
)

logger = logging.getLogger(__name__)

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


def _table_sql(conn: sqlite3.Connection, table_name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row["sql"] if row and row["sql"] else ""


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _create_minimal_galileo_agents_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS galileo_agents (
            id TEXT PRIMARY KEY,
            enterprise_id TEXT NOT NULL,
            event_id TEXT NOT NULL REFERENCES galileo_events(id),
            company_name TEXT NOT NULL,
            company_id TEXT NOT NULL REFERENCES galileo_companies(id),
            status TEXT NOT NULL DEFAULT 'Queued',
            outcome TEXT,
            ideal_price REAL NOT NULL DEFAULT 0,
            ceiling_price REAL NOT NULL DEFAULT 0,
            market_price REAL NOT NULL DEFAULT 0,
            current_price REAL NOT NULL DEFAULT 0,
            is_accepted INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def _ensure_base_galileo_columns(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "galileo_enterprises", "description", "TEXT")
    _ensure_column(conn, "galileo_enterprises", "total_saved_hotels", "REAL DEFAULT 0")
    _ensure_column(conn, "galileo_enterprises", "total_saved_airlines", "REAL DEFAULT 0")
    _ensure_column(conn, "galileo_enterprises", "total_saved", "REAL DEFAULT 0")
    _ensure_column(conn, "galileo_enterprises", "yoy_change", "REAL DEFAULT 0")
    _ensure_column(conn, "galileo_enterprises", "hotel_contract_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "galileo_enterprises", "airline_contract_count", "INTEGER DEFAULT 0")
    _ensure_column(conn, "galileo_companies", "initials", "TEXT")
    _ensure_column(conn, "galileo_companies", "description", "TEXT")
    _ensure_column(conn, "galileo_companies", "phone", "TEXT")
    _ensure_column(conn, "galileo_companies", "website", "TEXT")
    _ensure_column(conn, "galileo_companies", "industry", "TEXT")
    _ensure_column(conn, "galileo_companies", "badge", "TEXT")
    _ensure_column(conn, "galileo_events", "location", "TEXT")
    _ensure_column(conn, "galileo_events", "start_date", "TEXT")
    _ensure_column(conn, "galileo_events", "end_date", "TEXT")
    _ensure_column(conn, "galileo_events", "attendees", "INTEGER")
    _ensure_column(conn, "galileo_events", "status", "TEXT NOT NULL DEFAULT 'Active'")
    _ensure_column(conn, "galileo_events", "requirements", "TEXT")
    _ensure_column(conn, "galileo_events", "budget_per_person", "REAL")


def _migrate_negotiations_table(conn: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if "negotiations" not in tables:
        return

    ensure_negotiation_scaffold(conn)
    rows = conn.execute("SELECT * FROM negotiations").fetchall()
    for row in rows:
        vendor_name = row["vendor_name"]
        company_id = ensure_vendor_company(conn, vendor_name)
        config_text = row["config"]
        try:
            config_data = json.loads(config_text) if config_text else {}
        except json.JSONDecodeError:
            config_data = {}
        current_offer_text = row["current_offer"] if "current_offer" in row.keys() else None
        current_price = 0.0
        if current_offer_text:
            try:
                current_offer = json.loads(current_offer_text)
            except json.JSONDecodeError:
                current_offer = None
            if isinstance(current_offer, dict):
                current_price = float(current_offer.get("unit_price") or 0)

        conn.execute(
            """
            INSERT OR REPLACE INTO galileo_agents (
                id, enterprise_id, event_id, company_name, company_id, status, outcome,
                ideal_price, ceiling_price, market_price, current_price, is_accepted
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                NEGOTIATION_ENTERPRISE_ID,
                NEGOTIATION_EVENT_ID,
                vendor_name,
                company_id,
                row["status"],
                row["status"] if row["status"] in {"accepted", "rejected", "escalated"} else None,
                float(config_data.get("target_unit_price") or 0),
                float(config_data.get("max_unit_price") or 0),
                current_price,
                current_price,
                1 if row["status"] == "accepted" else 0,
            ),
        )
def _shrink_galileo_agents_table(conn: sqlite3.Connection) -> None:
    existing_columns = _table_columns(conn, "galileo_agents")
    desired = {
        "id",
        "enterprise_id",
        "event_id",
        "company_name",
        "company_id",
        "status",
        "outcome",
        "ideal_price",
        "ceiling_price",
        "market_price",
        "current_price",
        "is_accepted",
    }
    if existing_columns == desired:
        return

    legacy_table = "galileo_agents_legacy"
    conn.commit()  # PRAGMA foreign_keys only works outside a transaction
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(f"ALTER TABLE galileo_agents RENAME TO {legacy_table}")
    _create_minimal_galileo_agents_table(conn)

    def source(name: str, fallback: str) -> str:
        return name if name in existing_columns else fallback

    conn.execute(
        f"""
        INSERT INTO galileo_agents (
            id, enterprise_id, event_id, company_name, company_id, status, outcome,
            ideal_price, ceiling_price, market_price, current_price, is_accepted
        )
        SELECT
            id,
            enterprise_id,
            event_id,
            {source("company_name", "''")},
            company_id,
            {source("status", "'Queued'")},
            {source("outcome", "NULL")},
            {source("ideal_price", "0")},
            {source("ceiling_price", "0")},
            {source("market_price", source("original_price", "0"))},
            {source("current_price", "0")},
            {source("is_accepted", "0")}
        FROM {legacy_table}
        """
    )
    conn.execute(f"DROP TABLE {legacy_table}")
    conn.execute("PRAGMA foreign_keys=ON")


def _rebuild_table(
    conn: sqlite3.Connection,
    table_name: str,
    create_sql: str,
    columns: list[str],
    bad_ref: str = "REFERENCES negotiations",
) -> None:
    sql = _table_sql(conn, table_name)
    if not sql or bad_ref not in sql:
        return
    temp_name = f"{table_name}__rebuild_tmp"
    conn.execute(f"ALTER TABLE {table_name} RENAME TO {temp_name}")
    conn.execute(create_sql)
    column_list = ", ".join(columns)
    conn.execute(f"INSERT INTO {table_name} ({column_list}) SELECT {column_list} FROM {temp_name}")
    conn.execute(f"DROP TABLE {temp_name}")


def _drop_legacy_negotiations_table(conn: sqlite3.Connection) -> None:
    if "negotiations" not in {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }:
        return
    for table_name in ("messages", "call_sessions", "quote_events", "latest_quotes"):
        sql = _table_sql(conn, table_name)
        if sql and "REFERENCES negotiations" in sql:
            return
    conn.execute("DROP TABLE negotiations")


def _drop_negotiation_details_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS negotiation_details")


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_active_session_id_for_agent(agent_id: str) -> str | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM call_sessions WHERE negotiation_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (agent_id,),
        ).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _needs_schema_migration(conn, table: str, required_col: str) -> bool:
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if not cols:
        return False
    return not any(c[1] == required_col for c in cols)


def init_db() -> None:
    conn = get_db()

    for table in ("historic_pricing", "past_negotiations"):
        if _needs_schema_migration(conn, table, "month"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            logger.info("Dropped old-schema table %s for migration", table)

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS galileo_enterprises (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            total_saved_hotels REAL DEFAULT 0,
            total_saved_airlines REAL DEFAULT 0,
            total_saved REAL DEFAULT 0,
            yoy_change REAL DEFAULT 0,
            hotel_contract_count INTEGER DEFAULT 0,
            airline_contract_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS galileo_companies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            initials TEXT,
            description TEXT,
            phone TEXT,
            website TEXT,
            industry TEXT,
            badge TEXT
        );

        CREATE TABLE IF NOT EXISTS galileo_events (
            id TEXT PRIMARY KEY,
            enterprise_id TEXT NOT NULL REFERENCES galileo_enterprises(id),
            name TEXT NOT NULL,
            location TEXT,
            start_date TEXT,
            end_date TEXT,
            attendees INTEGER,
            service TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Active',
            requirements TEXT,
            budget_per_person REAL
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negotiation_id TEXT NOT NULL REFERENCES galileo_agents(id),
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
            negotiation_id TEXT NOT NULL REFERENCES galileo_agents(id),
            twilio_call_sid TEXT,
            status TEXT DEFAULT 'active',
            transcript TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS quote_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negotiation_id TEXT NOT NULL REFERENCES galileo_agents(id),
            vendor_name TEXT NOT NULL,
            product_category TEXT NOT NULL DEFAULT 'general',
            extracted_facts JSON,
            offer JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS latest_quotes (
            negotiation_id TEXT PRIMARY KEY REFERENCES galileo_agents(id),
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

    _ensure_base_galileo_columns(conn)
    _create_minimal_galileo_agents_table(conn)
    ensure_negotiation_scaffold(conn)
    _migrate_negotiations_table(conn)
    _shrink_galileo_agents_table(conn)

    # Fix FK references that got rewritten to galileo_agents_legacy during migration
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    _legacy_ref = "galileo_agents_legacy"
    _rebuild_table(
        conn,
        "galileo_price_points",
        """
        CREATE TABLE galileo_price_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
            label TEXT,
            price REAL,
            type TEXT,
            round INTEGER
        )
        """,
        ["id", "agent_id", "label", "price", "type", "round"],
        bad_ref=_legacy_ref,
    )
    _rebuild_table(
        conn,
        "galileo_activity_stream",
        """
        CREATE TABLE galileo_activity_stream (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
            price REAL,
            badge TEXT,
            badge_type TEXT,
            detail TEXT,
            detail_type TEXT,
            timestamp TEXT,
            active INTEGER DEFAULT 0
        )
        """,
        ["id", "agent_id", "price", "badge", "badge_type", "detail", "detail_type", "timestamp", "active"],
        bad_ref=_legacy_ref,
    )
    _rebuild_table(
        conn,
        "galileo_messages",
        """
        CREATE TABLE galileo_messages (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
            message TEXT,
            sender TEXT,
            timestamp TEXT
        )
        """,
        ["id", "agent_id", "message", "sender", "timestamp"],
        bad_ref=_legacy_ref,
    )
    _rebuild_table(
        conn,
        "galileo_previous_negotiations",
        """
        CREATE TABLE galileo_previous_negotiations (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
            contract_id TEXT,
            region TEXT,
            duration TEXT,
            final_rate REAL,
            total_savings REAL,
            status TEXT,
            start_date TEXT,
            end_date TEXT
        )
        """,
        ["id", "agent_id", "contract_id", "region", "duration", "final_rate", "total_savings", "status", "start_date", "end_date"],
        bad_ref=_legacy_ref,
    )

    _messages_sql = """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negotiation_id TEXT NOT NULL REFERENCES galileo_agents(id),
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            structured_data JSON,
            utility_score REAL,
            rag_context JSON,
            guardrail_log JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    _messages_cols = ["id", "negotiation_id", "role", "content", "structured_data", "utility_score", "rag_context", "guardrail_log", "created_at"]
    _call_sessions_sql = """
        CREATE TABLE call_sessions (
            id TEXT PRIMARY KEY,
            negotiation_id TEXT NOT NULL REFERENCES galileo_agents(id),
            twilio_call_sid TEXT,
            status TEXT DEFAULT 'active',
            transcript TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    _call_sessions_cols = ["id", "negotiation_id", "twilio_call_sid", "status", "transcript", "created_at"]
    _quote_events_sql = """
        CREATE TABLE quote_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            negotiation_id TEXT NOT NULL REFERENCES galileo_agents(id),
            vendor_name TEXT NOT NULL,
            product_category TEXT NOT NULL DEFAULT 'general',
            extracted_facts JSON,
            offer JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    _quote_events_cols = ["id", "negotiation_id", "vendor_name", "product_category", "extracted_facts", "offer", "created_at"]
    _latest_quotes_sql = """
        CREATE TABLE latest_quotes (
            negotiation_id TEXT PRIMARY KEY REFERENCES galileo_agents(id),
            vendor_name TEXT NOT NULL,
            product_category TEXT NOT NULL DEFAULT 'general',
            extracted_facts JSON,
            offer JSON,
            utility_score REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """

    # Fix legacy galileo_agents_legacy FK refs
    for tbl, sql, cols in [
        ("messages", _messages_sql, _messages_cols),
        ("call_sessions", _call_sessions_sql, _call_sessions_cols),
        ("quote_events", _quote_events_sql, _quote_events_cols),
        ("latest_quotes", _latest_quotes_sql, ["negotiation_id", "vendor_name", "product_category", "extracted_facts", "offer", "utility_score", "updated_at"]),
    ]:
        _rebuild_table(conn, tbl, sql, cols, bad_ref=_legacy_ref)

    # Fix old REFERENCES negotiations FK refs
    _rebuild_table(conn, "messages", _messages_sql, _messages_cols)
    _rebuild_table(conn, "call_sessions", _call_sessions_sql, _call_sessions_cols)
    _rebuild_table(conn, "quote_events", _quote_events_sql, _quote_events_cols)
    _rebuild_table(
        conn,
        "latest_quotes",
        _latest_quotes_sql,
        ["negotiation_id", "vendor_name", "product_category", "extracted_facts", "offer", "utility_score", "updated_at"],
    )
    conn.execute("PRAGMA foreign_keys=ON")
    _drop_negotiation_details_table(conn)
    _drop_legacy_negotiations_table(conn)

    conn.commit()
    conn.close()

    from app.memory.behavioral_store import migrate_call_history_from_chroma
    from app.services.rag import migrate_from_chroma

    chroma_dir = settings.chroma_persist_dir
    migrate_from_chroma(chroma_dir)
    migrate_call_history_from_chroma(chroma_dir)

    from app.services.market_data import seed_market_data, sync_market_data_to_redis

    seed_market_data()
    sync_market_data_to_redis()


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
    return _redis_client

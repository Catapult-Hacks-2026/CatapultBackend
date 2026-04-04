from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.database import get_db, get_redis
from app.services.rag import retrieve_vendor_context

_REDIS_PREFIX = "negotiation_memory"

logger = logging.getLogger(__name__)


def load_vendor_priors(vendor_name: str, product_category: str, top_k: int = 5) -> dict[str, Any]:
    history = retrieve_vendor_context(
        vendor_name,
        json.dumps({"product_category": product_category}),
        top_k=top_k,
    )
    discounts = [
        entry.get("discount_pct")
        for entry in history
        if isinstance(entry.get("discount_pct"), (int, float))
    ]
    return {
        "history": history,
        "average_discount_pct": round(sum(discounts) / len(discounts), 2) if discounts else None,
        "sample_size": len(history),
    }


def _redis_key(negotiation_id: str) -> str:
    return f"{_REDIS_PREFIX}:{negotiation_id}"


def _parse_memory_entry(raw: str) -> dict[str, Any]:
    entry = json.loads(raw)
    if isinstance(entry.get("structured_data"), str):
        try:
            entry["structured_data"] = json.loads(entry["structured_data"])
        except json.JSONDecodeError:
            pass
    return entry


def _rows_to_memory(rows: list) -> list[dict[str, Any]]:
    memory = []
    for row in rows:
        structured = None
        if row["structured_data"]:
            try:
                structured = json.loads(row["structured_data"])
            except json.JSONDecodeError:
                structured = row["structured_data"]
        memory.append(
            {
                "role": row["role"],
                "content": row["content"],
                "structured_data": structured,
                "created_at": row["created_at"],
            }
        )
    return memory


def _load_from_sqlite(negotiation_id: str, limit: int) -> list[dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT role, content, structured_data, created_at
        FROM messages
        WHERE negotiation_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (negotiation_id, limit),
    ).fetchall()
    conn.close()
    return _rows_to_memory(list(reversed(rows)))


def load_negotiation_working_memory(negotiation_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """Load recent negotiation messages from Redis, falling back to SQLite."""
    r = get_redis()
    key = _redis_key(negotiation_id)
    try:
        raw_entries = r.lrange(key, -limit, -1)
        if raw_entries:
            return [_parse_memory_entry(e) for e in raw_entries]
    except Exception:
        logger.warning("Redis read failed for %s, falling back to SQLite", negotiation_id)

    # Fallback: load from SQLite and backfill Redis
    memory = _load_from_sqlite(negotiation_id, limit)
    if memory:
        try:
            pipe = r.pipeline()
            pipe.delete(key)
            for entry in memory:
                pipe.rpush(key, json.dumps(entry))
            pipe.expire(key, 3600)
            pipe.execute()
        except Exception:
            logger.warning("Redis backfill failed for %s", negotiation_id)
    return memory


def refresh_negotiation_working_memory(negotiation_id: str) -> list[dict[str, Any]]:
    """Force-reload from SQLite and update the Redis cache."""
    r = get_redis()
    key = _redis_key(negotiation_id)
    memory = _load_from_sqlite(negotiation_id, limit=8)
    try:
        pipe = r.pipeline()
        pipe.delete(key)
        for entry in memory:
            pipe.rpush(key, json.dumps(entry))
        pipe.expire(key, 3600)
        pipe.execute()
    except Exception:
        logger.warning("Redis refresh failed for %s", negotiation_id)
    return memory


def extract_memory_candidates(
    vendor_name: str,
    transcript: list[dict[str, Any]],
    latest_offer: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not transcript and not latest_offer:
        return []
    key_lines = [turn["content"] for turn in transcript[-6:] if turn.get("content")]
    return [
        {
            "vendor_name": vendor_name,
            "summary": " | ".join(key_lines[-3:]),
            "latest_offer": latest_offer,
            "created_at": datetime.now(UTC).isoformat(),
        }
    ]


def store_memory_candidates(candidates: list[dict[str, Any]]) -> None:
    if not candidates:
        return
    r = get_redis()
    for candidate in candidates:
        vendor = candidate.get("vendor_name", "unknown")
        key = f"{_REDIS_PREFIX}:candidates:{vendor}"
        r.rpush(key, json.dumps(candidate))
        r.expire(key, 86400)
    logger.debug("Stored %d memory candidate(s)", len(candidates))

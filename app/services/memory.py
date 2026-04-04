from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.database import get_db
from app.services.rag import retrieve_vendor_context

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


def load_negotiation_working_memory_from_redis(negotiation_id: str, limit: int = 8) -> list[dict[str, Any]]:
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
    memory = []
    for row in reversed(rows):
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


def refresh_negotiation_working_memory(negotiation_id: str) -> list[dict[str, Any]]:
    return load_negotiation_working_memory_from_redis(negotiation_id)


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
    logger.debug("Stored %d memory candidate(s)", len(candidates))

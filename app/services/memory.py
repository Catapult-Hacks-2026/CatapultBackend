import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.cache import (
    get_negotiation_working_memory,
    get_vendor_working_memory,
    put_campaign_working_memory,
    put_category_working_memory,
    put_negotiation_working_memory,
    put_vendor_working_memory,
)
from app.core.config import get_settings
from app.core.database import get_db

settings = get_settings()


def _load_json(payload: str | None, default: Any) -> Any:
    if not payload:
        return default
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return default


async def extract_memory_candidates(
    negotiation_id: str,
    transcript: list[str],
    outcome: dict,
) -> list[dict]:
    conn = get_db()
    negotiation = conn.execute(
        "SELECT vendor_name, product_category FROM negotiations WHERE id = ?",
        (negotiation_id,),
    ).fetchone()
    conn.close()
    if negotiation is None:
        return []

    candidates: list[dict] = []
    lower_transcript = " ".join(transcript).lower()
    if "manager" in lower_transcript:
        candidates.append(
            {
                "vendor_name": negotiation["vendor_name"],
                "product_category": negotiation["product_category"],
                "pattern_type": "escalation_signal",
                "pattern_description": "Manager involvement occurred during negotiation",
                "confidence": 0.6,
                "source_negotiation_id": negotiation_id,
            }
        )
    final_price = outcome.get("quoted_rate") or outcome.get("unit_price")
    if final_price is not None:
        candidates.append(
            {
                "vendor_name": negotiation["vendor_name"],
                "product_category": negotiation["product_category"],
                "pattern_type": "price_band",
                "pattern_description": f"Quoted price band near {round(float(final_price), 2)}",
                "confidence": 0.7,
                "source_negotiation_id": negotiation_id,
            }
        )
    return candidates


async def store_memory_candidates(candidates: list[dict]) -> None:
    if not candidates:
        return
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    for candidate in candidates:
        existing = conn.execute(
            """
            SELECT * FROM memory_candidates
            WHERE vendor_name = ? AND product_category = ? AND pattern_type = ? AND pattern_description = ?
            """,
            (
                candidate["vendor_name"],
                candidate["product_category"],
                candidate["pattern_type"],
                candidate["pattern_description"],
            ),
        ).fetchone()
        if existing:
            source_ids = _load_json(existing["source_negotiation_ids"], [])
            if candidate["source_negotiation_id"] not in source_ids:
                source_ids.append(candidate["source_negotiation_id"])
            conn.execute(
                """
                UPDATE memory_candidates
                SET confidence = ?, evidence_count = evidence_count + 1, source_negotiation_ids = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    max(float(existing["confidence"]), float(candidate["confidence"])),
                    json.dumps(source_ids),
                    now,
                    existing["id"],
                ),
            )
            continue
        conn.execute(
            """
            INSERT INTO memory_candidates (
                id, vendor_name, product_category, pattern_type, pattern_description,
                confidence, evidence_count, validated, source_negotiation_ids, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                candidate["vendor_name"],
                candidate["product_category"],
                candidate["pattern_type"],
                candidate["pattern_description"],
                candidate["confidence"],
                json.dumps([candidate["source_negotiation_id"]]),
                now,
                now,
            ),
        )
    conn.commit()
    conn.close()


async def validate_memory_candidates() -> int:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM memory_candidates
        WHERE validated = 0
          AND evidence_count >= ?
          AND confidence >= ?
        ORDER BY updated_at ASC
        """,
        (settings.memory_validation_threshold, settings.memory_confidence_threshold),
    ).fetchall()
    promoted = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        feature_type = row["pattern_type"] or "behavioral_signal"
        feature_value = {
            "description": row["pattern_description"],
            "evidence_count": row["evidence_count"],
        }
        existing = conn.execute(
            """
            SELECT id FROM validated_features
            WHERE vendor_name = ? AND product_category = ? AND feature_type = ?
            """,
            (row["vendor_name"], row["product_category"], feature_type),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE validated_features
                SET feature_value = ?, confidence = ?, last_updated = ?
                WHERE id = ?
                """,
                (json.dumps(feature_value), row["confidence"], now, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO validated_features (
                    id, vendor_name, product_category, feature_type, feature_value, confidence, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    row["vendor_name"],
                    row["product_category"],
                    feature_type,
                    json.dumps(feature_value),
                    row["confidence"],
                    now,
                ),
            )
        conn.execute(
            "UPDATE memory_candidates SET validated = 1, updated_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        promoted += 1
    conn.commit()
    conn.close()
    return promoted


async def load_vendor_priors(vendor_name: str, product_category: str) -> dict:
    cached = await load_vendor_priors_from_redis(vendor_name, product_category)
    if cached is not None:
        return cached

    conn = get_db()
    rows = conn.execute(
        """
        SELECT feature_type, feature_value, confidence
        FROM validated_features
        WHERE vendor_name = ? AND (product_category = ? OR product_category IS NULL)
        ORDER BY last_updated DESC
        """,
        (vendor_name, product_category),
    ).fetchall()
    conn.close()
    priors = {}
    for row in rows:
        priors[row["feature_type"]] = {
            "value": _load_json(row["feature_value"], row["feature_value"]),
            "confidence": row["confidence"],
        }
    await store_vendor_priors_in_redis(
        vendor_name,
        product_category,
        priors,
        ttl=settings.working_memory_ttl,
    )
    return priors


async def load_vendor_priors_from_redis(vendor_name: str, product_category: str) -> dict | None:
    cached = await get_vendor_working_memory(vendor_name, product_category)
    if not isinstance(cached, dict):
        return None
    return cached.get("vendor_priors")


async def store_vendor_priors_in_redis(
    vendor_name: str,
    product_category: str,
    vendor_priors: dict,
    ttl: int | None = None,
) -> None:
    payload = {
        "vendor_name": vendor_name,
        "product_category": product_category,
        "vendor_priors": vendor_priors,
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "source": "sqlite_validated_features",
    }
    await put_vendor_working_memory(vendor_name, product_category, payload, ttl=ttl)


async def compile_vendor_working_memory_from_sqlite(
    vendor_name: str,
    product_category: str,
) -> dict:
    """
    Placeholder contract for Redis working-memory compilation.

    Intended implementation by teammate:
    1. Read validated_features, latest_quotes, and recent messages from SQLite.
    2. Optionally run a high-reasoning model to compress them into a low-latency payload.
    3. Return a JSON-serializable dict ready for Redis.
    """
    vendor_priors = await load_vendor_priors(vendor_name, product_category)
    return {
        "vendor_name": vendor_name,
        "product_category": product_category,
        "vendor_priors": vendor_priors,
        "recent_summary": None,
        "recommended_tactics": [],
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "source": "placeholder",
    }


async def refresh_vendor_working_memory(
    vendor_name: str,
    product_category: str,
    ttl: int | None = None,
) -> dict:
    payload = await compile_vendor_working_memory_from_sqlite(vendor_name, product_category)
    await put_vendor_working_memory(vendor_name, product_category, payload, ttl=ttl)
    return payload


async def load_negotiation_working_memory_from_redis(negotiation_id: str) -> dict | None:
    cached = await get_negotiation_working_memory(negotiation_id)
    if not isinstance(cached, dict):
        return None
    return cached


async def compile_negotiation_working_memory_from_sqlite(negotiation_id: str) -> dict | None:
    conn = get_db()
    negotiation = conn.execute(
        "SELECT * FROM negotiations WHERE id = ?",
        (negotiation_id,),
    ).fetchone()
    if negotiation is None:
        conn.close()
        return None
    messages = conn.execute(
        """
        SELECT role, content, extracted_facts, created_at
        FROM messages
        WHERE negotiation_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 12
        """,
        (negotiation_id,),
    ).fetchall()
    latest_quote = conn.execute(
        """
        SELECT vendor_name, product_category, unit_price, shipping_cost, payment_terms_days, delivery_days,
               confidence_score, restrictions, quote_timestamp
        FROM latest_quotes
        WHERE negotiation_id = ?
        ORDER BY quote_timestamp DESC
        LIMIT 1
        """,
        (negotiation_id,),
    ).fetchone()
    conn.close()
    return {
        "negotiation_id": negotiation_id,
        "vendor_name": negotiation["vendor_name"],
        "product_category": negotiation["product_category"],
        "buyer_config": _load_json(negotiation["config"], {}),
        "latest_quote": dict(latest_quote) if latest_quote is not None else None,
        "messages": [dict(row) for row in reversed(messages)],
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "source": "sqlite_negotiation_context",
    }


async def refresh_negotiation_working_memory(
    negotiation_id: str,
    ttl: int | None = None,
) -> dict | None:
    payload = await compile_negotiation_working_memory_from_sqlite(negotiation_id)
    if payload is None:
        return None
    await put_negotiation_working_memory(
        negotiation_id,
        payload,
        ttl=ttl or settings.working_memory_ttl,
    )
    return payload


async def store_negotiation_working_memory_in_redis(
    negotiation_id: str,
    payload: dict,
    ttl: int | None = None,
) -> None:
    await put_negotiation_working_memory(
        negotiation_id,
        payload,
        ttl=ttl or settings.working_memory_ttl,
    )


async def store_category_working_memory_in_redis(
    product_category: str,
    payload: dict,
    ttl: int | None = None,
) -> None:
    await put_category_working_memory(
        product_category,
        payload,
        ttl=ttl or settings.working_memory_ttl,
    )


async def refresh_category_working_memory(
    product_category: str,
    ttl: int | None = None,
) -> dict:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT vendor_name, unit_price, shipping_cost, payment_terms_days, delivery_days,
               confidence_score, quote_timestamp, negotiation_id
        FROM latest_quotes
        WHERE product_category = ?
        ORDER BY unit_price ASC, quote_timestamp DESC
        LIMIT 10
        """,
        (product_category,),
    ).fetchall()
    conn.close()
    payload = {
        "product_category": product_category,
        "best_quotes": [dict(row) for row in rows],
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "source": "sqlite_latest_quotes",
    }
    await store_category_working_memory_in_redis(
        product_category,
        payload,
        ttl=ttl,
    )
    return payload


async def store_campaign_working_memory_in_redis(
    campaign_id: str,
    payload: dict,
    ttl: int | None = None,
) -> None:
    await put_campaign_working_memory(
        campaign_id,
        payload,
        ttl=ttl or settings.working_memory_ttl,
    )


async def refresh_retrieval_summaries() -> int:
    return await validate_memory_candidates()

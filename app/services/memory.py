import json
import uuid
from datetime import datetime, timezone
from typing import Any

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
    return priors


async def refresh_retrieval_summaries() -> int:
    return await validate_memory_candidates()

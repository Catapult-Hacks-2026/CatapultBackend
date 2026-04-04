import json
from datetime import datetime, timezone
from typing import Optional

from app.core.config import get_settings
from app.core.database import get_db
from app.models.schemas import BuyerConfig
from app.services.llm import Anthropic, invoke_json
from app.services.rag import retrieve_competitor_context, retrieve_vendor_context

settings = get_settings()


def _parse_json_blob(payload: Optional[str]) -> Optional[dict]:
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _fallback_brief(negotiation: dict, config: BuyerConfig, vendor_history: list[dict]) -> dict:
    target_price = round(config.target_unit_price, 2)
    walk_away_price = round(config.max_unit_price, 2)
    target_delivery = int(config.preferred_delivery_days)
    min_terms = int(config.min_payment_terms)
    avg_discount = [
        entry["discount_pct"]
        for entry in vendor_history
        if isinstance(entry.get("discount_pct"), (int, float))
    ]
    avg_discount_pct = round(sum(avg_discount) / len(avg_discount), 2) if avg_discount else None
    return {
        "summary": (
            f"Push for a unit price near {target_price} in {negotiation['product_category']}, "
            f"protect payment terms of at least {min_terms} days, and avoid delivery beyond "
            f"{target_delivery} days."
        ),
        "vendor_patterns": [
            f"Historical discount average: {avg_discount_pct}%." if avg_discount_pct is not None else
            "No prior vendor-specific discounts found in history.",
            "Lead with price and delivery, then trade payment terms only if needed.",
        ],
        "recommended_anchor": {
            "unit_price": target_price,
            "shipping_cost": round(config.target_shipping_cost, 2),
            "payment_terms_days": int(config.preferred_payment_terms),
            "delivery_days": target_delivery,
        },
        "concession_plan": [
            "Start from target price and defend delivery reliability.",
            f"Do not exceed the walk-away price of {walk_away_price}.",
        ],
        "risk_flags": [
            "Escalate if the vendor insists on price above buyer max.",
            "Escalate if delivery extends beyond buyer maximum tolerance.",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "deterministic_fallback",
    }


def generate_negotiation_brief(negotiation_id: str) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT id, vendor_name, product_category, config, research_brief FROM negotiations WHERE id = ?",
        (negotiation_id,),
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Negotiation {negotiation_id} not found")

    existing = _parse_json_blob(row["research_brief"])
    if existing:
        return existing

    config = BuyerConfig.model_validate_json(row["config"])
    vendor_history = retrieve_vendor_context(
        row["vendor_name"],
        json.dumps({"product_category": row["product_category"]}),
        top_k=5,
    )
    competitor_history = retrieve_competitor_context(row["product_category"], top_k=5)

    if not settings.anthropic_api_key or Anthropic is None:
        brief = _fallback_brief(dict(row), config, vendor_history)
    else:
        system_prompt = (
            "You are generating a pre-call procurement research brief. Return valid JSON only with keys "
            '{"summary": string, "vendor_patterns": list[string], "recommended_anchor": {"unit_price": number, '
            '"shipping_cost": number, "payment_terms_days": integer, "delivery_days": integer}, '
            '"concession_plan": list[string], "risk_flags": list[string], "generated_at": string, '
            '"source": string}. Keep advice concise and concrete.'
        )
        user_prompt = json.dumps(
            {
                "vendor_name": row["vendor_name"],
                "product_category": row["product_category"],
                "buyer_constraints": config.model_dump(),
                "vendor_history": vendor_history,
                "competitor_history": competitor_history,
            },
            indent=2,
            default=str,
        )
        brief = invoke_json(
            system_prompt,
            user_prompt,
            model=settings.research_model or settings.negotiation_model,
            max_tokens=1200,
        )
        brief.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
        brief.setdefault("source", settings.research_model or settings.negotiation_model)

    conn = get_db()
    conn.execute(
        "UPDATE negotiations SET research_brief = ?, updated_at = ? WHERE id = ?",
        (json.dumps(brief), datetime.now(timezone.utc).isoformat(), negotiation_id),
    )
    conn.commit()
    conn.close()
    return brief

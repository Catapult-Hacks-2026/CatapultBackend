import json
from datetime import UTC, datetime

from app.core.database import get_redis
from app.models.schemas import VendorOffer

# Redis key conventions:
#   vendor_history:{negotiation_id}  -> hash with document, metadata
#   vendor_history:vendor:{name}     -> sorted set of negotiation_ids (scored by timestamp)
#   vendor_history:category:{cat}    -> sorted set of negotiation_ids (scored by timestamp)

_KEY_PREFIX = "vendor_history"


def _hash_key(negotiation_id: str) -> str:
    return f"{_KEY_PREFIX}:{negotiation_id}"


def _vendor_index_key(vendor_name: str) -> str:
    return f"{_KEY_PREFIX}:vendor:{vendor_name}"


def _category_index_key(category: str) -> str:
    return f"{_KEY_PREFIX}:category:{category}"


def _safe_discount_pct(messages: list[dict], final_offer: VendorOffer | dict) -> float | None:
    opening_price = None
    for message in messages:
        structured = message.get("structured_data") or {}
        if isinstance(structured, str):
            try:
                structured = json.loads(structured)
            except json.JSONDecodeError:
                structured = {}
        if isinstance(structured, dict) and structured.get("unit_price") is not None:
            opening_price = float(structured["unit_price"])
            break

    if opening_price in (None, 0):
        return None

    offer_data = final_offer.model_dump() if isinstance(final_offer, VendorOffer) else final_offer
    final_price = offer_data.get("unit_price")
    if final_price is None:
        return None
    return round(((opening_price - float(final_price)) / opening_price) * 100, 2)


def _build_summary(
    negotiation_id: str,
    vendor_name: str,
    messages: list[dict],
    final_offer: VendorOffer | dict | None,
    outcome: str,
) -> str:
    transcript = []
    for message in messages[-8:]:
        role = message.get("role", "unknown")
        content = message.get("content", "")
        transcript.append(f"{role}: {content}")
    offer_text = ""
    if final_offer is not None:
        payload = final_offer.model_dump() if isinstance(final_offer, VendorOffer) else final_offer
        offer_text = (
            f" Final offer: unit_price={payload.get('unit_price')},"
            f" shipping_cost={payload.get('shipping_cost')},"
            f" payment_terms_days={payload.get('payment_terms_days')},"
            f" delivery_days={payload.get('delivery_days')}."
        )
    return (
        f"Negotiation {negotiation_id} with {vendor_name}. Outcome: {outcome}."
        f"{offer_text} Conversation summary: {' | '.join(transcript)}"
    )


def add_negotiation_to_history(
    negotiation_id: str,
    vendor_name: str,
    product_category: str,
    messages: list[dict],
    final_offer: VendorOffer | dict | None,
    outcome: str,
) -> None:
    r = get_redis()
    final_offer_data = (
        final_offer.model_dump() if isinstance(final_offer, VendorOffer) else final_offer or {}
    )
    discount_pct = _safe_discount_pct(messages, final_offer_data)
    document = _build_summary(negotiation_id, vendor_name, messages, final_offer_data, outcome)
    now = datetime.now(UTC)
    score = now.timestamp()

    metadata = {
        "vendor_name": vendor_name,
        "product_category": product_category,
        "date": now.date().isoformat(),
        "outcome": outcome,
        "final_unit_price": final_offer_data.get("unit_price") or "",
        "discount_pct": discount_pct if discount_pct is not None else "",
        "deal_type": final_offer_data.get("notes") or "standard",
    }

    pipe = r.pipeline()
    pipe.hset(
        _hash_key(negotiation_id),
        mapping={"document": document, "metadata": json.dumps(metadata)},
    )
    pipe.zadd(_vendor_index_key(vendor_name), {negotiation_id: score})
    pipe.zadd(_category_index_key(product_category), {negotiation_id: score})
    pipe.execute()


def _fetch_entries(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    r = get_redis()
    pipe = r.pipeline()
    for nid in ids:
        pipe.hgetall(_hash_key(nid))
    results = pipe.execute()

    entries = []
    for raw in results:
        if not raw:
            continue
        meta = json.loads(raw.get("metadata", "{}"))
        discount = meta.get("discount_pct")
        entries.append(
            {
                "text": raw.get("document", ""),
                "vendor": meta.get("vendor_name", "unknown"),
                "date": meta.get("date", "unknown"),
                "deal_type": meta.get("deal_type", "unknown"),
                "discount_pct": discount if discount != "" else None,
                "outcome": meta.get("outcome"),
                "final_unit_price": meta.get("final_unit_price") or None,
                "distance": None,
            }
        )
    return entries


def retrieve_vendor_context(vendor_name: str, current_offer_text: str, top_k: int = 5) -> list[dict]:
    r = get_redis()
    ids = r.zrevrange(_vendor_index_key(vendor_name), 0, top_k - 1)
    return _fetch_entries(ids)


def retrieve_competitor_context(product_category: str, top_k: int = 5) -> list[dict]:
    r = get_redis()
    ids = r.zrevrange(_category_index_key(product_category), 0, top_k - 1)
    return _fetch_entries(ids)

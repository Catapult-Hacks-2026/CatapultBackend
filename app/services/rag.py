import json
from datetime import UTC, datetime

from app.core.database import get_vendor_collection
from app.models.schemas import VendorOffer


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
    collection = get_vendor_collection()
    final_offer_data = (
        final_offer.model_dump() if isinstance(final_offer, VendorOffer) else final_offer or {}
    )
    discount_pct = _safe_discount_pct(messages, final_offer_data)
    document = _build_summary(negotiation_id, vendor_name, messages, final_offer_data, outcome)
    metadata = {
        "vendor_name": vendor_name,
        "product_category": product_category,
        "date": datetime.now(UTC).date().isoformat(),
        "outcome": outcome,
        "final_unit_price": final_offer_data.get("unit_price"),
        "discount_pct": discount_pct,
        "deal_type": final_offer_data.get("notes") or "standard",
    }
    collection.upsert(
        ids=[negotiation_id],
        documents=[document],
        metadatas=[metadata],
    )


def _format_query_result(results: dict) -> list[dict]:
    context = []
    if not results or not results.get("documents"):
        return context

    documents = results["documents"][0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    for i, document in enumerate(documents):
        meta = metadatas[i] if i < len(metadatas) else {}
        distance = distances[i] if i < len(distances) else None
        context.append(
            {
                "text": document,
                "vendor": meta.get("vendor_name", "unknown"),
                "date": meta.get("date", "unknown"),
                "deal_type": meta.get("deal_type", "unknown"),
                "discount_pct": meta.get("discount_pct"),
                "outcome": meta.get("outcome"),
                "final_unit_price": meta.get("final_unit_price"),
                "distance": distance,
            }
        )
    return context


def retrieve_vendor_context(vendor_name: str, current_offer_text: str, top_k: int = 5) -> list[dict]:
    collection = get_vendor_collection()
    if collection.count() == 0:
        return []
    results = collection.query(
        query_texts=[f"{vendor_name}: {current_offer_text}"],
        n_results=top_k,
        where={"vendor_name": vendor_name},
        include=["documents", "metadatas", "distances"],
    )
    return _format_query_result(results)


def retrieve_competitor_context(product_category: str, top_k: int = 5) -> list[dict]:
    collection = get_vendor_collection()
    if collection.count() == 0:
        return []
    query = {
        "query_texts": [f"Historical negotiations for {product_category}"],
        "n_results": top_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if product_category:
        query["where"] = {"product_category": product_category}
    results = collection.query(**query)
    return _format_query_result(results)

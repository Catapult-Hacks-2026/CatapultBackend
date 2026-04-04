"""RAG service — ChromaDB vendor history retrieval.

Stub: needs full implementation. See IMPLEMENTATION_PLAN.md Task 3.
"""

from app.core.database import get_vendor_collection


def retrieve_vendor_context(vendor_name: str, current_offer_text: str, top_k: int = 5) -> list[dict]:
    """Query ChromaDB for historical context relevant to this vendor/offer."""
    collection = get_vendor_collection()
    if collection.count() == 0:
        return []

    results = collection.query(
        query_texts=[f"{vendor_name}: {current_offer_text}"],
        n_results=top_k,
    )

    context = []
    if results and results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            context.append({
                "text": doc,
                "vendor": meta.get("vendor_name", "unknown"),
                "date": meta.get("date", "unknown"),
                "deal_type": meta.get("deal_type", "unknown"),
                "discount_pct": meta.get("discount_pct"),
            })
    return context

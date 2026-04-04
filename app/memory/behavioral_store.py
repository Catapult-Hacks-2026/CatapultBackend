from __future__ import annotations

import logging
from datetime import datetime, timezone

import chromadb
import httpx

from app.core.config import get_settings
from app.hotel.enums import NegotiationOutcome
from app.hotel.schemas import HotelQuote
from app.memory.feature_schemas import HotelBehavioralFeature, HotelBehavioralProfile

logger = logging.getLogger(__name__)

_CHROMA_COLLECTION = "hotel_call_history"

_client: chromadb.ClientAPI | None = None


def _get_chroma() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=get_settings().chroma_persist_dir)
    return _client


def _get_collection() -> chromadb.Collection:
    return _get_chroma().get_or_create_collection(_CHROMA_COLLECTION)


def _backend_url(path: str) -> str:
    return f"{get_settings().base_url}{path}"


def _apply_time_decay(features: list[HotelBehavioralFeature]) -> list[HotelBehavioralFeature]:
    now = datetime.now(timezone.utc)
    decayed = []
    for f in features:
        days_old = (now - f.observed_at).total_seconds() / 86400
        adjusted_confidence = f.confidence * ((1 - f.decay_rate) ** days_old)
        decayed.append(f.model_copy(update={"confidence": max(0.0, adjusted_confidence)}))
    return decayed


class BehavioralStore:
    async def load_priors(self, hotel_id: str) -> HotelBehavioralProfile:
        """Aggregate structured features from backend + recent summaries from ChromaDB."""
        features: list[HotelBehavioralFeature] = []
        profile_data: dict = {}

        # Structured features from backend
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(_backend_url(f"/api/memory/priors/{hotel_id}"))
                if resp.status_code == 200:
                    payload = resp.json()
                    profile_data = payload.get("profile", {})
                    for item in payload.get("features", []):
                        try:
                            features.append(HotelBehavioralFeature(**item))
                        except Exception:
                            pass
            except Exception as exc:
                logger.warning("load_priors: backend unavailable for hotel %s: %s", hotel_id, exc)

        # Apply time-decay to confidence scores
        features = _apply_time_decay(features)

        # Semantic search in ChromaDB for recent call summaries
        chroma_summary = self._load_chroma_summaries(hotel_id)
        if chroma_summary:
            features.append(HotelBehavioralFeature(
                hotel_id=hotel_id,
                feature_type="leverage",
                feature_key="recent_call_context",
                value=chroma_summary,
                confidence=0.7,
            ))

        return HotelBehavioralProfile(
            hotel_id=hotel_id,
            features=features,
            last_call_date=profile_data.get("last_call_date"),
            total_calls=profile_data.get("total_calls", 0),
            success_rate=profile_data.get("success_rate", 0.0),
            avg_negotiated_discount=profile_data.get("avg_negotiated_discount", 0.0),
            best_rate=profile_data.get("best_rate"),
            worst_rate=profile_data.get("worst_rate"),
            common_objections=profile_data.get("common_objections", []),
            escalation_success_rate=profile_data.get("escalation_success_rate", 0.0),
        )

    def _load_chroma_summaries(self, hotel_id: str, top_k: int = 3) -> str:
        try:
            collection = _get_collection()
            results = collection.query(
                query_texts=[f"hotel {hotel_id} rate negotiation"],
                n_results=top_k,
                where={"hotel_id": hotel_id},
            )
            docs = results.get("documents", [[]])[0]
            if not docs:
                return ""
            return " | ".join(docs)
        except Exception as exc:
            logger.warning("ChromaDB query failed for hotel %s: %s", hotel_id, exc)
            return ""

    async def store_call_summary(
        self,
        session_id: str,
        hotel_id: str,
        summary: str,
        outcome: NegotiationOutcome,
        quotes: list[HotelQuote],
    ) -> None:
        best_rate = min((q.nightly_rate for q in quotes), default=None)
        doc_text = (
            f"Hotel: {hotel_id}. Outcome: {outcome.value}. Summary: {summary}."
            + (f" Best rate: ${best_rate:.2f}/night." if best_rate else "")
        )
        try:
            collection = _get_collection()
            collection.upsert(
                ids=[session_id],
                documents=[doc_text],
                metadatas=[{
                    "hotel_id": hotel_id,
                    "outcome": outcome.value,
                    "best_rate": str(best_rate) if best_rate else "",
                    "session_id": session_id,
                }],
            )
        except Exception as exc:
            logger.error("ChromaDB upsert failed for session %s: %s", session_id, exc)

    async def store_features(self, features: list[HotelBehavioralFeature]) -> None:
        if not features:
            return
        payload = [f.model_dump(mode="json") for f in features]
        async with httpx.AsyncClient() as client:
            try:
                await client.post(_backend_url("/api/memory/features/"), json=payload)
            except Exception as exc:
                logger.error("store_features: POST failed: %s", exc)

    async def search_similar_hotels(
        self, hotel_id: str, market: str, top_k: int = 5
    ) -> list[str]:
        """Return session summaries from hotels with similar negotiation profiles."""
        try:
            collection = _get_collection()
            results = collection.query(
                query_texts=[f"{market} hotel rate negotiation similar to {hotel_id}"],
                n_results=top_k,
            )
            return results.get("documents", [[]])[0]
        except Exception as exc:
            logger.warning("search_similar_hotels failed: %s", exc)
            return []


_store: BehavioralStore | None = None


def get_behavioral_store() -> BehavioralStore:
    global _store
    if _store is None:
        _store = BehavioralStore()
    return _store

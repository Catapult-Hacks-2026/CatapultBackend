from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx

from app.core.config import get_settings
from app.core.database import get_redis
from app.hotel.enums import NegotiationOutcome
from app.hotel.schemas import HotelQuote
from app.memory.feature_schemas import HotelBehavioralFeature, HotelBehavioralProfile

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "hotel_call_history"


def _hash_key(session_id: str) -> str:
    return f"{_REDIS_PREFIX}:{session_id}"


def _hotel_index_key(hotel_id: str) -> str:
    return f"{_REDIS_PREFIX}:hotel:{hotel_id}"


def _market_index_key(market: str) -> str:
    return f"{_REDIS_PREFIX}:market:{market}"


def _backend_url(path: str) -> str:
    return build_upstream_url(path)


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
        """Aggregate structured features from backend + recent summaries from Redis."""
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

        # Load recent call summaries from Redis
        redis_summary = self._load_redis_summaries(hotel_id)
        if redis_summary:
            features.append(HotelBehavioralFeature(
                hotel_id=hotel_id,
                feature_type="leverage",
                feature_key="recent_call_context",
                value=redis_summary,
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

    def _load_redis_summaries(self, hotel_id: str, top_k: int = 3) -> str:
        try:
            r = get_redis()
            session_ids = r.zrevrange(_hotel_index_key(hotel_id), 0, top_k - 1)
            if not session_ids:
                return ""
            pipe = r.pipeline()
            for sid in session_ids:
                pipe.hget(_hash_key(sid), "document")
            docs = pipe.execute()
            return " | ".join(d for d in docs if d)
        except Exception as exc:
            logger.warning("Redis query failed for hotel %s: %s", hotel_id, exc)
            return ""

    async def store_session_summary(
        self,
        session_id: str,
        hotel_id: str,
        summary: str,
        outcome,
        quotes: list[HotelQuote],
        market: str = "",
    ) -> None:
        outcome_value = outcome.value if hasattr(outcome, "value") else str(outcome)
        best_rate = min((q.nightly_rate for q in quotes), default=None)
        doc_text = (
            f"Hotel: {hotel_id}. Outcome: {outcome_value}. Summary: {summary}."
            + (f" Best rate: ${best_rate:.2f}/night." if best_rate else "")
        )
        metadata = {
            "hotel_id": hotel_id,
            "outcome": outcome.value,
            "best_rate": str(best_rate) if best_rate else "",
            "session_id": session_id,
        }
        try:
            r = get_redis()
            score = datetime.now(timezone.utc).timestamp()
            pipe = r.pipeline()
            pipe.hset(
                _hash_key(session_id),
                mapping={"document": doc_text, "metadata": json.dumps(metadata)},
            )
            pipe.zadd(_hotel_index_key(hotel_id), {session_id: score})
            if market:
                pipe.zadd(_market_index_key(market), {session_id: score})
            pipe.execute()
        except Exception as exc:
            logger.error("Redis upsert failed for session %s: %s", session_id, exc)

    async def store_call_summary(
        self,
        session_id: str,
        hotel_id: str,
        summary: str,
        outcome: NegotiationOutcome,
        quotes: list[HotelQuote],
    ) -> None:
        await self.store_session_summary(session_id, hotel_id, summary, outcome, quotes)

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
            r = get_redis()
            session_ids = r.zrevrange(_market_index_key(market), 0, top_k - 1)
            if not session_ids:
                return []
            pipe = r.pipeline()
            for sid in session_ids:
                pipe.hget(_hash_key(sid), "document")
            docs = pipe.execute()
            return [d for d in docs if d]
        except Exception as exc:
            logger.warning("search_similar_hotels failed: %s", exc)
            return []


_MIGRATION_SENTINEL = f"{_REDIS_PREFIX}:_migrated_from_chroma"


def migrate_call_history_from_chroma(chroma_dir: str = "data/chroma") -> int:
    """One-time migration: copy hotel_call_history records from ChromaDB into Redis.

    Returns the number of records migrated. Uses a sentinel key to track
    completion so partial migrations or new data don't prevent re-running.
    """
    from pathlib import Path

    chroma_path = Path(chroma_dir)
    if not chroma_path.exists():
        return 0

    r = get_redis()
    if r.exists(_MIGRATION_SENTINEL):
        return 0

    try:
        import chromadb
    except ImportError:
        logger.warning(
            "migrate_call_history: chromadb not installed, cannot migrate legacy data. "
            "Install chromadb or run `python -m app.seed` to re-seed."
        )
        return 0

    try:
        client = chromadb.PersistentClient(path=str(chroma_path))
        collection = client.get_collection("hotel_call_history")
    except Exception:
        logger.debug("migrate_call_history: no hotel_call_history collection found in ChromaDB")
        return 0

    all_records = collection.get(include=["documents", "metadatas"])
    ids = all_records.get("ids", [])
    documents = all_records.get("documents", [])
    metadatas = all_records.get("metadatas", [])

    if not ids:
        r.set(_MIGRATION_SENTINEL, "1")
        return 0

    migrated = 0
    pipe = r.pipeline()
    for i, session_id in enumerate(ids):
        doc = documents[i] if i < len(documents) else ""
        meta = metadatas[i] if i < len(metadatas) else {}

        hotel_id = meta.get("hotel_id", "unknown")

        pipe.hset(
            _hash_key(session_id),
            mapping={"document": doc, "metadata": json.dumps(meta)},
        )
        pipe.zadd(_hotel_index_key(hotel_id), {session_id: float(i)})
        migrated += 1

    pipe.set(_MIGRATION_SENTINEL, "1")
    pipe.execute()
    logger.info("migrate_call_history: migrated %d records from ChromaDB to Redis", migrated)
    return migrated


_store: BehavioralStore | None = None


def get_behavioral_store() -> BehavioralStore:
    global _store
    if _store is None:
        _store = BehavioralStore()
    return _store

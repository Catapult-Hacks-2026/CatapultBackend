from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.hotel.schemas import WorkerSessionState
from app.llm.openai_client import invoke_json
from app.memory.feature_schemas import HotelBehavioralFeature

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM = """\
You are extracting structured behavioral features from a hotel rate negotiation transcript.
These features will be stored and used to improve future negotiations with this hotel.

Return a JSON object with a single key "features" containing a list of objects, each with:
- feature_type: category of the observation. One of:
    "price_flexibility"   — rate movement patterns
    "authority_limit"     — who can approve what rate
    "timing"              — time-of-day or day-of-week patterns
    "objection"           — recurring objections or stall tactics
    "leverage"            — what arguments moved the needle
- feature_key: short snake_case identifier (e.g. "weekday_rate_floor", "manager_required_below_180")
- value: the observed fact as a string (e.g. "dropped 12% after competitor mention", "$180/night")
- confidence: float 0-1 based on how clearly this was demonstrated in the call

Only include features that are clearly supported by the transcript. Return {"features": []} if none found.
"""


async def extract_memory_candidates(
    session_state: WorkerSessionState,
) -> list[HotelBehavioralFeature]:
    return await extract_memory_candidates_from_transcript(
        session_id=session_state.session_id,
        hotel_id=session_state.hotel_target.hotel_id,
        transcript=session_state.transcript,
    )


async def extract_memory_candidates_from_transcript(
    session_id: str,
    hotel_id: str,
    transcript: list[dict[str, str]],
) -> list[HotelBehavioralFeature]:
    transcript_text = "\n".join(
        f"{t['role']}: {t['content']}" for t in transcript
    )
    if not transcript_text.strip():
        return []

    try:
        data = await invoke_json(_EXTRACTION_SYSTEM, transcript_text, temperature=0.0)
    except Exception:
        logger.exception("Memory candidate extraction failed for session %s", session_id)
        return []

    features = []
    now = datetime.now(timezone.utc)
    for item in data.get("features", []):
        try:
            features.append(HotelBehavioralFeature(
                hotel_id=hotel_id,
                feature_type=item["feature_type"],
                feature_key=item["feature_key"],
                value=item["value"],
                confidence=float(item.get("confidence", 0.8)),
                source_session_id=session_id,
                observed_at=now,
            ))
        except Exception:
            logger.warning("Skipping malformed feature item: %s", item)

    return features

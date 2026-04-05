from __future__ import annotations

import logging

from app.hotel.schemas import HotelQuote, WorkerSessionState
from app.llm.openai_client import invoke_json
from app.llm.prompts import FACT_EXTRACTION_SYSTEM

logger = logging.getLogger(__name__)

# Use the fast model for all extractions; escalate only when result is ambiguous
_FAST_MODEL = "gpt-4o-mini"
_FULL_MODEL = "gpt-4o"


def _result_is_ambiguous(data: dict) -> bool:
    """True if the fast model returned a rate but no raw_text to verify it against."""
    return data.get("nightly_rate") is not None and not data.get("raw_text")


async def extract_facts_from_utterance(
    utterance: str,
    session_state: WorkerSessionState,
) -> HotelQuote | None:
    recent = session_state.transcript[-4:] if len(session_state.transcript) >= 4 else session_state.transcript
    context_lines = "\n".join(f"{t['role']}: {t['content']}" for t in recent)
    user_prompt = f"Conversation so far:\n{context_lines}"

    try:
        data = await invoke_json(FACT_EXTRACTION_SYSTEM, user_prompt, model=_FAST_MODEL, temperature=0.0)

        # Escalate to full model if the fast model found a rate but couldn't ground it
        if _result_is_ambiguous(data):
            data = await invoke_json(FACT_EXTRACTION_SYSTEM, user_prompt, model=_FULL_MODEL, temperature=0.0)
    except Exception:
        return None

    logger.info(
        "=== FACT EXTRACTION === utterance=%r extracted nightly_rate=%s raw_text=%r",
        utterance,
        data.get("nightly_rate"),
        data.get("raw_text"),
    )

    if data.get("nightly_rate") is None:
        return None

    return HotelQuote(
        nightly_rate=float(data["nightly_rate"]),
        total_rate=float(data.get("total_rate") or data["nightly_rate"]),
        inclusions=data.get("inclusions") or {},
        cancellation_policy=data.get("cancellation_policy") or "",
        rate_type=data.get("rate_type") or "",
        fees=float(data.get("fees") or 0),
        raw_text=data.get("raw_text") or utterance,
    )

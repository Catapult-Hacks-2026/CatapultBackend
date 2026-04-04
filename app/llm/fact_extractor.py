from __future__ import annotations

import json

from app.hotel.schemas import HotelQuote, WorkerSessionState
from app.llm.openai_client import invoke_json
from app.llm.prompts import FACT_EXTRACTION_SYSTEM


async def extract_facts_from_utterance(
    utterance: str,
    session_state: WorkerSessionState,
) -> HotelQuote | None:
    # The utterance is already the last item in the transcript (appended before this call).
    # Use the last 4 turns for context — the final hotel turn is the one to extract from.
    recent = session_state.transcript[-4:] if len(session_state.transcript) >= 4 else session_state.transcript
    context_lines = "\n".join(f"{t['role']}: {t['content']}" for t in recent)

    user_prompt = f"Conversation so far:\n{context_lines}"

    try:
        data = await invoke_json(FACT_EXTRACTION_SYSTEM, user_prompt, temperature=0.0)
    except Exception:
        return None

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

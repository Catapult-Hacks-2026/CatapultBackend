from __future__ import annotations

import asyncio

from app.llm.client import async_invoke_json
from app.llm.prompts import FACT_EXTRACTION_SYSTEM
from app.models.schemas import ExtractedFacts


async def extract_facts(
    utterance: str,
    session_state: dict,
) -> ExtractedFacts | None:
    transcript = session_state.get("transcript", [])
    recent = transcript[-4:] if len(transcript) >= 4 else transcript
    context_lines = "\n".join(f"{t['role']}: {t['content']}" for t in recent)
    user_prompt = f"Conversation so far:\n{context_lines}\n\nCurrent vendor utterance:\n{utterance}"

    try:
        data = await async_invoke_json(FACT_EXTRACTION_SYSTEM, user_prompt, temperature=0.0)
    except Exception:
        return None

    facts = ExtractedFacts.model_validate(data)
    if facts.unit_price is None and facts.shipping_cost is None and facts.payment_terms_days is None:
        return None
    return facts


async def extract_facts_from_utterance(utterance: str, session_state: dict) -> ExtractedFacts | None:
    return await asyncio.create_task(extract_facts(utterance, session_state))

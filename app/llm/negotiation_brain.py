from __future__ import annotations

import json
from typing import AsyncGenerator

from app.llm.client import invoke_json, stream_text
from app.llm.prompts import (
    NEGOTIATION_BRAIN_SYSTEM,
    RESPONSE_GENERATION_SYSTEM,
    STRATEGY_INSTRUCTIONS,
)
from app.models.enums import NegotiationAction, Strategy
from app.models.schemas import AgentAction


def _build_brain_context(negotiation_context: dict) -> str:
    strategy = negotiation_context.get("strategy", Strategy.BALANCED.value)
    strategy_instruction = STRATEGY_INSTRUCTIONS.get(
        str(strategy),
        STRATEGY_INSTRUCTIONS[Strategy.BALANCED.value],
    )
    payload = {
        "vendor_name": negotiation_context.get("vendor_name"),
        "product_category": negotiation_context.get("product_category"),
        "buyer_config": negotiation_context.get("buyer_config"),
        "current_offer": negotiation_context.get("current_offer"),
        "scoring_breakdown": negotiation_context.get("scoring_breakdown"),
        "round_number": negotiation_context.get("round_number"),
        "conversation_history": negotiation_context.get("conversation_history"),
        "vendor_priors": negotiation_context.get("vendor_priors"),
        "rag_context": negotiation_context.get("rag_context"),
        "research_brief": negotiation_context.get("research_brief"),
        "competing_offers": negotiation_context.get("competing_offers"),
        "guardrail_feedback": negotiation_context.get("guardrail_feedback"),
        "strategy_instruction": strategy_instruction,
    }
    return json.dumps(payload, indent=2, default=str)


def decide_move(negotiation_context: dict) -> AgentAction:
    payload = invoke_json(
        NEGOTIATION_BRAIN_SYSTEM,
        _build_brain_context(negotiation_context),
        temperature=0.2,
    )
    if "action" not in payload:
        payload["action"] = NegotiationAction.COUNTER.value
    return AgentAction.model_validate(payload)


async def generate_response_streaming(
    action: AgentAction,
    session_state: dict,
) -> AsyncGenerator[str, None]:
    transcript = session_state.get("transcript", [])
    context_lines = transcript[-4:] if len(transcript) >= 4 else transcript
    transcript_text = "\n".join(f"{t['role']}: {t['content']}" for t in context_lines)
    user_prompt = json.dumps(
        {
            "action": action.action.value,
            "message_draft": action.message,
            "counter_offer": action.counter_offer.model_dump() if action.counter_offer else None,
            "recent_conversation": transcript_text,
        },
        indent=2,
    )
    async for token in stream_text(RESPONSE_GENERATION_SYSTEM, user_prompt):
        yield token

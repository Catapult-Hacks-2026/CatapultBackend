from __future__ import annotations

import json
from typing import AsyncGenerator

from app.hotel.enums import MoveType
from app.hotel.schemas import AgentMove, HotelQuote, WorkerSessionState
from app.hotel.scoring import score_hotel_quote
from app.llm.openai_client import invoke_json, stream_text
from app.llm.prompts import NEGOTIATION_BRAIN_SYSTEM, RESPONSE_GENERATION_SYSTEM


def _build_brain_context(session_state: WorkerSessionState) -> str:
    target = session_state.hotel_target
    lines = [
        f"Check-in: {target.check_in}, Check-out: {target.check_out}",
        f"Room type: {target.room_type}",
        f"Moves so far: {len(session_state.moves_made)}",
    ]

    if session_state.quotes_received:
        best = min(session_state.quotes_received, key=lambda q: q.nightly_rate)
        breakdown = score_hotel_quote(best, target)
        # Score relative to target without revealing the raw target number
        if breakdown.rate_score >= 1.0:
            rate_assessment = "below target (excellent)"
        elif breakdown.rate_score >= 0.6:
            rate_assessment = "near target (acceptable)"
        else:
            rate_assessment = "above target (push further)"
        lines.append(
            f"Best quote: ${best.nightly_rate}/night — {rate_assessment} "
            f"(overall score: {breakdown.total:.2f}/1.0)"
        )
        if best.inclusions:
            lines.append(f"Inclusions: {', '.join(k for k, v in best.inclusions.items() if v)}")
        if best.cancellation_policy:
            lines.append(f"Cancellation: {best.cancellation_policy}")
    else:
        lines.append("No quotes received yet — open the negotiation.")

    # Include only negotiation-relevant priors, not raw API dumps
    priors = session_state.behavioral_priors
    prior_summary = priors.get("negotiation_summary")
    if prior_summary:
        lines.append(f"Prior call patterns: {prior_summary}")

    # Market intelligence: historic rates and past deal outcomes from Redis
    market_brief = priors.get("market_brief")
    if market_brief:
        lines.append(f"\n{market_brief}")

    transcript_lines = session_state.transcript[-6:] if len(session_state.transcript) >= 6 else session_state.transcript
    lines.append("\nRecent conversation:")
    for t in transcript_lines:
        lines.append(f"  {t['role']}: {t['content']}")

    return "\n".join(lines)


async def decide_move(
    session_state: WorkerSessionState,
    guardrail_feedback: str | None = None,
) -> AgentMove:
    context = _build_brain_context(session_state)
    if guardrail_feedback:
        context += (
            f"\n\nIMPORTANT — your previous response was REJECTED by guardrails: "
            f"{guardrail_feedback}. You MUST avoid this violation in your next response."
        )
    data = await invoke_json(
        NEGOTIATION_BRAIN_SYSTEM, context,
        model="gpt-4.1-mini", temperature=0.2, max_tokens=512,
    )

    move_type = MoveType(data.get("move_type", MoveType.PROBE))
    counter_rate = data.get("counter_rate")

    return AgentMove(
        move_type=move_type,
        response_text="",  # filled by generate_response_streaming
        reasoning=data.get("reasoning", ""),
        should_terminate=bool(data.get("should_terminate", False)),
        counter_rate=float(counter_rate) if counter_rate is not None else None,
    )


async def generate_response_streaming(
    move: AgentMove,
    session_state: WorkerSessionState,
) -> AsyncGenerator[str, None]:
    target = session_state.hotel_target
    context_lines = session_state.transcript[-4:] if len(session_state.transcript) >= 4 else session_state.transcript
    transcript_text = "\n".join(f"{t['role']}: {t['content']}" for t in context_lines)

    counter_hint = ""
    if move.counter_rate is not None:
        counter_hint = f"Counter with ${move.counter_rate}/night. "

    user_prompt = (
        f"Move type: {move.move_type.value}\n"
        f"{counter_hint}"
        f"Recent conversation:\n{transcript_text}"
    )

    return stream_text(RESPONSE_GENERATION_SYSTEM, user_prompt, max_tokens=200)

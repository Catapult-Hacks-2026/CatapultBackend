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
        f"Hotel: {target.hotel_id}",
        f"Check-in: {target.check_in}, Check-out: {target.check_out}",
        f"Room type: {target.room_type}",
        f"Target rate: ${target.target_rate}/night",
        f"Moves so far: {len(session_state.moves_made)}",
    ]

    if session_state.quotes_received:
        best = min(session_state.quotes_received, key=lambda q: q.nightly_rate)
        breakdown = score_hotel_quote(best, target)
        lines.append(f"Best quote: ${best.nightly_rate}/night (score: {breakdown.total:.2f})")

    if session_state.behavioral_priors:
        lines.append(f"Behavioral priors: {json.dumps(session_state.behavioral_priors)}")

    transcript_lines = session_state.transcript[-6:] if len(session_state.transcript) >= 6 else session_state.transcript
    lines.append("\nRecent transcript:")
    for t in transcript_lines:
        lines.append(f"  {t['role']}: {t['content']}")

    return "\n".join(lines)


async def decide_move(session_state: WorkerSessionState) -> AgentMove:
    context = _build_brain_context(session_state)
    data = await invoke_json(NEGOTIATION_BRAIN_SYSTEM, context, temperature=0.2)

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
        f"Internal reasoning: {move.reasoning}\n\n"
        f"Recent conversation:\n{transcript_text}"
    )

    return stream_text(RESPONSE_GENERATION_SYSTEM, user_prompt)

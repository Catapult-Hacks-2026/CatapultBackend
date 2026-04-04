from __future__ import annotations

from app.email.schemas import EmailExtractionResult, EmailSessionState
from app.hotel.enums import MoveType
from app.hotel.schemas import AgentMove
from app.hotel.scoring import score_hotel_quote
from app.llm.openai_client import invoke_json, invoke_text
from app.llm.prompts import EMAIL_NEGOTIATION_BRAIN_SYSTEM, EMAIL_RESPONSE_GENERATION_SYSTEM


def _build_email_context(session_state: EmailSessionState, extraction: EmailExtractionResult) -> str:
    target = session_state.email_target
    lines = [
        f"Check-in: {target.check_in}",
        f"Check-out: {target.check_out}",
        f"Room type: {target.room_type}",
        f"Hotel email: {target.email_address}",
        f"Moves so far: {len(session_state.moves_made)}",
    ]

    if extraction.quote is not None:
        breakdown = score_hotel_quote(extraction.quote, target)
        lines.append(
            f"Latest quote: ${extraction.quote.nightly_rate}/night "
            f"(score {breakdown.total:.2f}/1.0)"
        )
    elif session_state.quotes_received:
        best = min(session_state.quotes_received, key=lambda quote: quote.nightly_rate)
        lines.append(f"Best quote so far: ${best.nightly_rate}/night")
    else:
        lines.append("No quote received yet.")

    prior_summary = session_state.behavioral_priors.get("negotiation_summary")
    if prior_summary:
        lines.append(f"Prior patterns: {prior_summary}")

    transcript = session_state.transcript[-6:] if len(session_state.transcript) >= 6 else session_state.transcript
    lines.append("\nRecent thread:")
    for item in transcript:
        lines.append(f"{item['role']}: {item['content']}")

    return "\n".join(lines)


async def decide_email_move(
    session_state: EmailSessionState,
    extraction: EmailExtractionResult,
) -> AgentMove:
    context = _build_email_context(session_state, extraction)
    try:
        data = await invoke_json(EMAIL_NEGOTIATION_BRAIN_SYSTEM, context, temperature=0.2)
        move_type = MoveType(data.get("move_type", MoveType.PROBE))
        counter_rate = data.get("counter_rate")
        return AgentMove(
            move_type=move_type,
            response_text="",
            reasoning=data.get("reasoning", ""),
            should_terminate=bool(data.get("should_terminate", False)),
            should_escalate=bool(data.get("should_escalate", False)),
            escalation_reason=data.get("escalation_reason", ""),
            counter_rate=float(counter_rate) if counter_rate is not None else None,
        )
    except Exception:
        return _fallback_move(session_state, extraction)


async def generate_email_reply(
    move: AgentMove,
    session_state: EmailSessionState,
    extraction: EmailExtractionResult,
) -> str:
    transcript = session_state.transcript[-4:] if len(session_state.transcript) >= 4 else session_state.transcript
    transcript_text = "\n".join(f"{item['role']}: {item['content']}" for item in transcript)
    quote_hint = ""
    if extraction.quote is not None:
        quote_hint = f"Latest quoted rate: ${extraction.quote.nightly_rate}/night.\n"
    counter_hint = f"Counter target: ${move.counter_rate}/night.\n" if move.counter_rate is not None else ""
    user_prompt = (
        f"Move type: {move.move_type.value}\n"
        f"{quote_hint}{counter_hint}"
        f"Recent thread:\n{transcript_text}"
    )
    return (await invoke_text(EMAIL_RESPONSE_GENERATION_SYSTEM, user_prompt, temperature=0.5)).strip()


def _fallback_move(session_state: EmailSessionState, extraction: EmailExtractionResult) -> AgentMove:
    target = session_state.email_target
    if extraction.booking_ready:
        return AgentMove(
            move_type=MoveType.CLOSE,
            response_text="",
            reasoning="booking confirmation should be escalated",
            should_terminate=True,
            should_escalate=True,
            escalation_reason="booking confirmation requested",
        )

    if extraction.quote is None:
        move_type = MoveType.OPEN if not session_state.moves_made else MoveType.PROBE
        return AgentMove(move_type=move_type, response_text="", reasoning="requesting quote details")

    if extraction.quote.nightly_rate <= target.target_rate:
        return AgentMove(
            move_type=MoveType.ACCEPT,
            response_text="",
            reasoning="quote is at or below target",
            should_terminate=True,
        )

    counter_rate = max(target.target_rate, min(target.max_rate, extraction.quote.nightly_rate - 10))
    return AgentMove(
        move_type=MoveType.COUNTER,
        response_text="",
        reasoning="quote above target, counter politely",
        counter_rate=counter_rate,
    )

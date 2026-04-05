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
    try:
        return (await invoke_text(EMAIL_RESPONSE_GENERATION_SYSTEM, user_prompt, temperature=0.5)).strip()
    except Exception:
        return _fallback_reply(move, session_state, extraction)


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


def _fallback_reply(
    move: AgentMove,
    session_state: EmailSessionState,
    extraction: EmailExtractionResult,
) -> str:
    target = session_state.email_target
    contact_name = target.contact_name.strip()
    greeting = f"Hello {contact_name}," if contact_name else "Hello,"
    stay = f"{target.check_in} to {target.check_out}"
    room_line = f" for a {target.room_type} stay" if target.room_type else ""

    if move.move_type == MoveType.ACCEPT:
        nightly_rate = extraction.quote.nightly_rate if extraction.quote is not None else target.target_rate
        return (
            f"{greeting}\n\n"
            f"Thank you for confirming the rate. ${nightly_rate:.0f} per night works for the requested stay "
            f"({stay}){room_line}. Please send over the next non-payment steps and any standard terms for review.\n\n"
            "Best,\nCatapult Travel"
        )

    if move.move_type == MoveType.COUNTER and move.counter_rate is not None:
        return (
            f"{greeting}\n\n"
            f"Thank you for the update. We would be interested in moving forward if you can do "
            f"${move.counter_rate:.0f} per night for the requested stay ({stay}){room_line}. "
            "Please let me know if that is workable.\n\n"
            "Best,\nCatapult Travel"
        )

    if move.move_type in {MoveType.PROBE, MoveType.ANCHOR, MoveType.CONCEDE}:
        return (
            f"{greeting}\n\n"
            f"Thank you for the details. Could you share your best available nightly rate{room_line} "
            f"for {stay}, along with any inclusions or fees?\n\n"
            "Best,\nCatapult Travel"
        )

    if move.move_type == MoveType.CLOSE or move.should_escalate:
        return (
            f"{greeting}\n\n"
            "Thank you for the information. I need to route the next steps internally and will follow up shortly.\n\n"
            "Best,\nCatapult Travel"
        )

    return (
        f"{greeting}\n\n"
        f"I’m reaching out regarding availability and rates{room_line} for {stay}. "
        "Could you share your best available offer?\n\n"
        "Best,\nCatapult Travel"
    )

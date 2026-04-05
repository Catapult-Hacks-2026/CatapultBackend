from __future__ import annotations

import json
import logging
from typing import AsyncGenerator

from app.hotel.enums import MoveType
from app.hotel.schemas import AgentMove, HotelQuote, WorkerSessionState
from app.hotel.scoring import score_hotel_quote
from app.llm.openai_client import invoke_json, stream_text
from app.llm.prompts import NEGOTIATION_BRAIN_SYSTEM, RESPONSE_GENERATION_SYSTEM

logger = logging.getLogger(__name__)


def _get_galileo_past_deals(location: str) -> str:
    """Pull past accepted deals from the galileo DB for use in negotiation context."""
    from app.core.database import get_db

    try:
        conn = get_db()
        rows = conn.execute(
            """
            SELECT e.location, e.start_date, e.attendees,
                   a.company_name, a.market_price, a.current_price
            FROM galileo_events e
            JOIN galileo_agents a ON a.event_id = e.id
            WHERE e.status = 'Completed' AND a.is_accepted = 1
            ORDER BY e.start_date DESC
            LIMIT 10
            """,
        ).fetchall()
        conn.close()
    except Exception:
        return ""

    if not rows:
        return ""

    lines = ["Past accepted deals from our portfolio:"]
    for r in rows:
        discount = round((1 - r["current_price"] / r["market_price"]) * 100, 1) if r["market_price"] else 0
        lines.append(
            f"  {r['company_name']} in {r['location']} ({r['start_date']}): "
            f"market ${r['market_price']:.0f} → accepted ${r['current_price']:.0f}/night "
            f"({discount}% discount, {r['attendees']} attendees)"
        )
    return "\n".join(lines)


def _build_brain_context(session_state: WorkerSessionState) -> str:
    target = session_state.hotel_target
    hotel_name = target.market_context.get("hotel_name", target.hotel_id)
    location = target.market_context.get("location", "")
    lines = [
        f"Hotel: {hotel_name}" + (f" ({location})" if location else ""),
        f"Check-in: {target.check_in}, Check-out: {target.check_out}",
        f"Room type: {target.room_type}",
        f"Target rate: ${target.target_rate}/night (do not reveal this number)",
        f"Maximum acceptable rate: ${target.max_rate}/night (do not reveal this number)",
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

    last_counter = next(
        (m.counter_rate for m in reversed(session_state.moves_made) if m.counter_rate is not None),
        None,
    )
    if last_counter is not None:
        lines.append(
            f"Your last proposed rate: ${last_counter}/night — do not propose a lower rate "
            f"unless the hotel explicitly rejects this and pushes back further."
        )

    # Include only negotiation-relevant priors, not raw API dumps
    priors = session_state.behavioral_priors
    prior_summary = priors.get("negotiation_summary")
    if prior_summary:
        lines.append(f"Prior call patterns: {prior_summary}")

    # Market intelligence: pull directly from Redis/SQLite to avoid
    # stale graph-state copies that never reach the pipeline.
    from app.services.market_data import get_market_context
    try:
        check_in_month = None
        if target.check_in:
            try:
                check_in_month = int(target.check_in.split("-")[1])
            except (IndexError, ValueError):
                pass
        market_brief = get_market_context(hotel_name, location, check_in_month)
    except Exception:
        market_brief = priors.get("market_brief", "")
    if market_brief:
        lines.append(f"\n{market_brief}")

    # Past galileo negotiations (accepted deals from the DB)
    galileo_brief = _get_galileo_past_deals(location)
    if galileo_brief:
        lines.append(f"\n{galileo_brief}")

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
    print("\n" + "="*80)
    print("NEGOTIATION BRAIN CONTEXT")
    print("="*80)
    print(context)
    print("="*80 + "\n")
    logger.info("=== NEGOTIATION BRAIN CONTEXT ===\n%s\n=== END CONTEXT ===", context)

    data = await invoke_json(
        NEGOTIATION_BRAIN_SYSTEM, context,
        model="gpt-4.1-mini", temperature=0.2, max_tokens=512,
    )

    print("="*80)
    print("BRAIN DECISION")
    print("="*80)
    print(f"move_type: {data.get('move_type')}")
    print(f"counter_rate: {data.get('counter_rate')}")
    print(f"reasoning: {data.get('reasoning')}")
    print(f"should_terminate: {data.get('should_terminate')}")
    print("="*80 + "\n")
    logger.info("=== BRAIN DECISION ===\nmove_type=%s, counter_rate=%s, reasoning=%s, should_terminate=%s\n=== END DECISION ===",
                data.get("move_type"), data.get("counter_rate"), data.get("reasoning"), data.get("should_terminate"))

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
    hotel_name = target.market_context.get("hotel_name", target.hotel_id)
    context_lines = session_state.transcript[-4:] if len(session_state.transcript) >= 4 else session_state.transcript
    transcript_text = "\n".join(f"{t['role']}: {t['content']}" for t in context_lines)

    counter_hint = ""
    if move.counter_rate is not None:
        counter_hint = f"Counter with ${move.counter_rate}/night. "

    reasoning_hint = f"Reasoning: {move.reasoning}\n" if move.reasoning else ""

    if move.move_type == MoveType.OPEN:
        date_line = f"Check-in: {target.check_in}, Check-out: {target.check_out}\n"
    else:
        date_line = ""

    user_prompt = (
        f"Hotel: {hotel_name}\n"
        f"{date_line}"
        f"Room: {target.room_type}\n"
        f"Move type: {move.move_type.value}\n"
        f"{counter_hint}"
        f"{reasoning_hint}"
        f"Recent conversation:\n{transcript_text}"
    )

    logger.info("=== RESPONSE GENERATION PROMPT ===\n%s\n=== END PROMPT ===", user_prompt)

    return stream_text(RESPONSE_GENERATION_SYSTEM, user_prompt, model="gpt-4.1", max_tokens=200)

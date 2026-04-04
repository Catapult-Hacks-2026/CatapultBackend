from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.hotel.enums import MoveType
from app.hotel.schemas import AgentMove, HotelTarget, WorkerSessionState


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str = ""


_BUDGET_LEAK_PATTERNS = [
    r"\bmax(imum)?\s*(rate|budget|price)\b",
    r"\bwe('re| are) willing to pay up to\b",
    r"\bbudget\s+is\b",
    r"\bcan('t| not) go (above|over|higher than)\b",
]
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _BUDGET_LEAK_PATTERNS]


def validate_agent_move(
    move: AgentMove,
    target: HotelTarget,
    session_state: WorkerSessionState,
) -> GuardrailResult:
    # No accepting on the first exchange
    if move.move_type == MoveType.ACCEPT and len(session_state.moves_made) == 0:
        return GuardrailResult(allowed=False, reason="cannot accept on first exchange")

    # Counter rate must not exceed max_rate
    if move.counter_rate is not None and move.counter_rate > target.max_rate:
        return GuardrailResult(
            allowed=False,
            reason=f"counter_rate {move.counter_rate} exceeds max_rate {target.max_rate}",
        )

    # Accepted quote must not exceed max_rate
    if move.move_type == MoveType.ACCEPT and move.extracted_quote is not None:
        if move.extracted_quote.nightly_rate > target.max_rate:
            return GuardrailResult(
                allowed=False,
                reason=f"accepted rate {move.extracted_quote.nightly_rate} exceeds max_rate {target.max_rate}",
            )

    # No budget leaks in response text
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(move.response_text):
            return GuardrailResult(
                allowed=False,
                reason=f"response text leaks budget information (matched: {pattern.pattern})",
            )

    return GuardrailResult(allowed=True)

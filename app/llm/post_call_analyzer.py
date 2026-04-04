from __future__ import annotations

from pydantic import BaseModel, Field

from app.hotel.enums import NegotiationOutcome
from app.hotel.schemas import WorkerSessionState
from app.llm.openai_client import invoke_json
from app.llm.prompts import POST_CALL_ANALYSIS_SYSTEM


class PostCallAnalysis(BaseModel):
    summary: str
    outcome: NegotiationOutcome
    key_patterns: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    call_quality_score: float = 0.0     # 0-1: how well the agent performed
    follow_up_recommended: bool = False
    follow_up_reason: str = ""


async def analyze_call(session_state: WorkerSessionState) -> PostCallAnalysis:
    transcript_text = "\n".join(
        f"{t['role']}: {t['content']}" for t in session_state.transcript
    )
    moves_summary = f"Total moves: {len(session_state.moves_made)}"
    best_quote_text = ""
    if session_state.quotes_received:
        best = min(session_state.quotes_received, key=lambda q: q.nightly_rate)
        best_quote_text = f"Best quote received: ${best.nightly_rate}/night"

    user_prompt = (
        f"{moves_summary}\n"
        f"{best_quote_text}\n\n"
        f"Full transcript:\n{transcript_text}"
    )

    data = await invoke_json(POST_CALL_ANALYSIS_SYSTEM, user_prompt, temperature=0.0)

    outcome_str = data.get("outcome", "failed")
    try:
        outcome = NegotiationOutcome(outcome_str)
    except ValueError:
        outcome = NegotiationOutcome.FAILED

    return PostCallAnalysis(
        summary=data.get("summary", ""),
        outcome=outcome,
        key_patterns=data.get("key_patterns", []),
        lessons=data.get("lessons", []),
        call_quality_score=float(data.get("call_quality_score", 0.0)),
        follow_up_recommended=bool(data.get("follow_up_recommended", False)),
        follow_up_reason=data.get("follow_up_reason", ""),
    )

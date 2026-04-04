from __future__ import annotations

from pydantic import BaseModel, Field

from app.email.enums import EmailOutcome
from app.email.schemas import EmailSessionState
from app.llm.openai_client import invoke_json
from app.llm.prompts import EMAIL_THREAD_ANALYSIS_SYSTEM


class PostEmailAnalysis(BaseModel):
    summary: str
    outcome: EmailOutcome
    key_patterns: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    follow_up_recommended: bool = False
    follow_up_reason: str = ""


async def analyze_email_thread(session_state: EmailSessionState) -> PostEmailAnalysis:
    transcript_text = "\n".join(
        f"{item['role']}: {item['content']}" for item in session_state.transcript
    )
    user_prompt = f"Subject: {session_state.subject}\n\nThread:\n{transcript_text}"

    try:
        data = await invoke_json(EMAIL_THREAD_ANALYSIS_SYSTEM, user_prompt, temperature=0.0)
        outcome_raw = data.get("outcome", EmailOutcome.FAILED.value)
        try:
            outcome = EmailOutcome(outcome_raw)
        except ValueError:
            outcome = session_state.outcome or EmailOutcome.FAILED
    except Exception:
        data = {}
        outcome = session_state.outcome or EmailOutcome.FAILED

    return PostEmailAnalysis(
        summary=data.get("summary", ""),
        outcome=outcome,
        key_patterns=data.get("key_patterns", []),
        lessons=data.get("lessons", []),
        follow_up_recommended=bool(data.get("follow_up_recommended", False)),
        follow_up_reason=data.get("follow_up_reason", ""),
    )

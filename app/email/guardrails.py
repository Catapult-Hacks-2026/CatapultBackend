from __future__ import annotations

from dataclasses import dataclass

from app.email.parser import contains_booking_language, contains_policy_or_legal_language
from app.email.schemas import EmailExtractionResult, EmailMessage, EmailSessionState
from app.hotel.schemas import AgentMove


@dataclass
class EmailGuardrailResult:
    allow_auto_send: bool
    reason: str = ""
    terminal_outcome: str = ""


def validate_email_turn(
    session_state: EmailSessionState,
    inbound_message: EmailMessage,
    extraction: EmailExtractionResult,
    move: AgentMove,
) -> EmailGuardrailResult:
    latest_text = inbound_message.text

    if inbound_message.attachment_count > 0:
        return EmailGuardrailResult(False, reason="attachments require human review", terminal_outcome="needs_human")

    if extraction.requests_human_action or move.should_escalate:
        reason = move.escalation_reason or "message requires human action"
        return EmailGuardrailResult(False, reason=reason, terminal_outcome="needs_human")

    if extraction.booking_ready or contains_booking_language(latest_text):
        return EmailGuardrailResult(False, reason="booking confirmation requires human approval", terminal_outcome="booking_ready")

    if contains_policy_or_legal_language(latest_text):
        return EmailGuardrailResult(False, reason="policy or legal content requires human review", terminal_outcome="needs_human")

    if extraction.confidence and extraction.confidence < 0.45:
        return EmailGuardrailResult(False, reason="low-confidence extraction", terminal_outcome="needs_human")

    if len(session_state.moves_made) >= 8 and not extraction.quote:
        return EmailGuardrailResult(False, reason="thread is dragging without a concrete quote", terminal_outcome="needs_human")

    return EmailGuardrailResult(True)

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.artifacts.schemas import EmailAttachment, NegotiatedRateAgreement, ReceiptArtifacts
from app.email.enums import EmailDirection, EmailOutcome, EmailSessionStatus
from app.hotel.schemas import AgentMove, HotelQuote


class EmailTarget(BaseModel):
    channel: str = "email"
    hotel_id: str
    email_address: str
    contact_name: str = ""
    check_in: str
    check_out: str
    room_type: str
    target_rate: float
    max_rate: float
    priority_score: float = 0.0
    market_context: dict[str, Any] = Field(default_factory=dict)
    campaign_metadata: dict[str, Any] = Field(default_factory=dict)


class EmailMessage(BaseModel):
    provider: str
    direction: EmailDirection
    subject: str
    text: str
    from_address: str
    to_address: str
    provider_message_id: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = Field(default_factory=list)
    attachment_count: int = 0
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmailExtractionResult(BaseModel):
    quote: HotelQuote | None = None
    raw_text: str = ""
    confidence: float = 0.0
    booking_ready: bool = False
    no_availability: bool = False
    requests_human_action: bool = False


class EmailSessionState(BaseModel):
    session_id: str
    campaign_id: str = ""
    email_target: EmailTarget
    status: EmailSessionStatus = EmailSessionStatus.INITIALIZING
    outcome: EmailOutcome | None = None
    subject: str = ""
    reply_address: str = ""
    external_thread_id: str = ""
    behavioral_priors: dict[str, Any] = Field(default_factory=dict)
    last_inbound_message: EmailMessage | None = None
    last_outbound_message: EmailMessage | None = None
    messages: list[EmailMessage] = Field(default_factory=list)
    transcript: list[dict[str, str]] = Field(default_factory=list)
    quotes_received: list[HotelQuote] = Field(default_factory=list)
    moves_made: list[AgentMove] = Field(default_factory=list)
    escalation_reason: str = ""
    needs_human_review: bool = False
    error_log: list[str] = Field(default_factory=list)
    contract_details: NegotiatedRateAgreement | None = None
    receipt_artifacts: ReceiptArtifacts | None = None


class OutboundEmail(BaseModel):
    to_address: str
    subject: str
    text: str
    reply_to: str = ""
    in_reply_to: str = ""
    references: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    attachments: list[EmailAttachment] = Field(default_factory=list)


class SentEmailReceipt(BaseModel):
    provider: str
    provider_message_id: str = ""
    message_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

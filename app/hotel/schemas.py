from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.artifacts.schemas import NegotiatedRateAgreement, ReceiptArtifacts
from app.hotel.enums import MoveType, NegotiationOutcome, SessionStatus


class HotelTarget(BaseModel):
    channel: str = "voice"
    hotel_id: str
    phone_number: str
    check_in: str
    check_out: str
    room_type: str
    target_rate: float
    max_rate: float
    priority_score: float = 0.0
    market_context: dict[str, Any] = Field(default_factory=dict)
    campaign_metadata: dict[str, Any] = Field(default_factory=dict)


class HotelQuote(BaseModel):
    nightly_rate: float
    total_rate: float
    inclusions: dict[str, bool] = Field(default_factory=dict)  # breakfast, wifi, parking, etc.
    cancellation_policy: str = ""
    rate_type: str = ""
    fees: float = 0.0
    raw_text: str = ""


class AgentMove(BaseModel):
    move_type: MoveType
    response_text: str
    reasoning: str = ""
    extracted_quote: HotelQuote | None = None
    should_terminate: bool = False
    should_escalate: bool = False
    escalation_reason: str = ""
    counter_rate: float | None = None


class WorkerSessionState(BaseModel):
    session_id: str
    campaign_id: str = ""
    hotel_target: HotelTarget
    status: SessionStatus = SessionStatus.INITIALIZING
    call_sid: str = ""
    lock_acquired: bool = False
    quotes_received: list[HotelQuote] = Field(default_factory=list)
    moves_made: list[AgentMove] = Field(default_factory=list)
    transcript: list[dict[str, str]] = Field(default_factory=list)
    behavioral_priors: dict[str, Any] = Field(default_factory=dict)
    next_move: AgentMove | None = None
    error_log: list[str] = Field(default_factory=list)
    outcome: NegotiationOutcome | None = None
    contract_details: NegotiatedRateAgreement | None = None
    receipt_artifacts: ReceiptArtifacts | None = None


class WorkerResult(BaseModel):
    session_id: str
    status: str
    outcome: str | None
    best_quote: HotelQuote | None
    transcript: list[dict[str, str]]
    moves_made: list[AgentMove]
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)
    contract_details: NegotiatedRateAgreement | None = None
    receipt_artifacts: ReceiptArtifacts | None = None

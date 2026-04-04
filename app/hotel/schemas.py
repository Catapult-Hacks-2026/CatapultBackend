from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.hotel.enums import MoveType, NegotiationOutcome, SessionStatus


class HotelTarget(BaseModel):
    hotel_id: str
    phone_number: str
    check_in: str
    check_out: str
    room_type: str
    target_rate: float
    max_rate: float
    priority_score: float = 0.0
    market_context: dict[str, Any] = Field(default_factory=dict)


class HotelQuote(BaseModel):
    nightly_rate: float
    total_rate: float
    inclusions: dict[str, bool] = Field(default_factory=dict)  # breakfast, wifi, etc.
    cancellation_policy: str = ""
    rate_type: str = ""
    fees: float = 0.0
    raw_text: str = ""
    confidence: float = 1.0


class AgentMove(BaseModel):
    move_type: MoveType
    response_text: str
    reasoning: str = ""
    extracted_quote: HotelQuote | None = None
    should_terminate: bool = False
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


class WorkerResult(BaseModel):
    session_id: str
    status: SessionStatus
    outcome: NegotiationOutcome | None
    best_quote: HotelQuote | None
    transcript: list[dict[str, str]]
    moves_made: list[AgentMove]
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)

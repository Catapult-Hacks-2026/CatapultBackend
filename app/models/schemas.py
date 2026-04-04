from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.enums import CampaignStatus, Strategy, WorkerEventType


class BuyerConfig(BaseModel):
    target_unit_price: float
    max_unit_price: float
    target_shipping_cost: float = 0.0
    max_shipping_cost: float = 1000.0
    preferred_payment_terms: int = 60
    min_payment_terms: int = 30
    preferred_delivery_days: int = 14
    max_delivery_days: int = 30
    quantity: int = 1000
    weight_price: float = 0.45
    weight_shipping: float = 0.15
    weight_payment_terms: float = 0.20
    weight_delivery: float = 0.20
    min_acceptable_utility: float = 0.6


class VendorOffer(BaseModel):
    unit_price: float
    shipping_cost: float = 0.0
    payment_terms_days: int = 30
    delivery_days: int = 14
    notes: Optional[str] = None


class CreateNegotiationRequest(BaseModel):
    vendor_name: str
    strategy: Strategy = Strategy.BALANCED
    product_category: str = "general"
    config: BuyerConfig


class InboundVendorMessage(BaseModel):
    negotiation_id: str
    message: str
    offer: Optional[VendorOffer] = None


class NegotiationResponse(BaseModel):
    id: str
    vendor_name: str
    product_category: str
    status: str
    strategy: str
    round_number: int
    utility_score: Optional[float]
    current_offer: Optional[VendorOffer]
    campaign_id: Optional[str] = None
    thread_id: Optional[str] = None
    worker_status: str = "idle"
    manager_reached: bool = False
    callback_requested: bool = False
    final_outcome: Optional[str] = None


class BatchVendorConfig(BaseModel):
    vendor_name: str
    strategy: Strategy = Strategy.BALANCED
    vendor_phone_number: Optional[str] = None


class BatchNegotiationRequest(BaseModel):
    product_category: str = "general"
    config: BuyerConfig
    vendors: list[BatchVendorConfig]
    auto_call: bool = False


class BatchNegotiationResult(BaseModel):
    negotiation: NegotiationResponse
    call_status: Optional[dict] = None


class BatchNegotiationResponse(BaseModel):
    items: list[BatchNegotiationResult]


class AgentAction(BaseModel):
    counter_offer: Optional[VendorOffer] = None
    message: str
    reasoning: str
    should_accept: bool = False
    should_escalate: bool = False


class GuardrailResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)
    adjusted_action: Optional[AgentAction] = None


class ScoringBreakdown(BaseModel):
    raw_scores: dict[str, float]
    weighted_scores: dict[str, float]
    total_utility: float
    meets_threshold: bool


class CampaignTarget(BaseModel):
    vendor_name: str
    vendor_phone: Optional[str] = None
    product_category: str = "general"
    priority: float = 0.5
    deadline: Optional[datetime] = None
    config: BuyerConfig


class CreateCampaignRequest(BaseModel):
    name: str
    targets: list[CampaignTarget]
    max_concurrent_workers: int = 5
    max_budget: Optional[float] = None
    auto_start: bool = False


class CampaignResponse(BaseModel):
    id: str
    name: str
    status: CampaignStatus
    total_jobs: int
    active_workers: int
    completed_jobs: int
    failed_jobs: int
    max_concurrent_workers: int
    max_budget: Optional[float] = None


class CampaignSummary(BaseModel):
    campaign_id: str
    best_quotes: list[dict[str, Any]]
    pending_negotiations: int
    retry_schedules: list[dict[str, Any]]
    unreached_vendors: list[str]
    estimated_savings: float


class WorkerEvent(BaseModel):
    event_type: WorkerEventType
    negotiation_id: str
    campaign_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class ExtractedFacts(BaseModel):
    quoted_rate: Optional[float] = None
    taxes: Optional[float] = None
    fees: Optional[dict[str, Any]] = None
    product_details: Optional[str] = None
    cancellation_terms: Optional[str] = None
    discount_authority: Optional[str] = None
    refusals: list[str] = Field(default_factory=list)
    negotiation_openness: float = 0.5
    payment_terms_days: Optional[int] = None
    delivery_days: Optional[int] = None
    shipping_cost: Optional[float] = None


class MemoryCandidate(BaseModel):
    vendor_name: str
    product_category: str
    pattern_type: str
    pattern_description: str
    confidence: float
    source_negotiation_id: str

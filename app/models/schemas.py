from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.enums import NegotiationAction, Strategy


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
    action: NegotiationAction = NegotiationAction.COUNTER
    counter_offer: Optional[VendorOffer] = None
    message: str
    reasoning: str
    should_accept: bool = False
    should_escalate: bool = False


class ExtractedFacts(BaseModel):
    unit_price: Optional[float] = None
    shipping_cost: Optional[float] = None
    payment_terms_days: Optional[int] = None
    delivery_days: Optional[int] = None
    fees: list[str] = Field(default_factory=list)
    discount_authority: Optional[str] = None
    negotiation_openness: Optional[str] = None
    refusals: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    raw_text: str = ""

    def to_vendor_offer(self, default: VendorOffer | None = None) -> VendorOffer | None:
        if self.unit_price is None and default is None:
            return None
        return VendorOffer(
            unit_price=float(self.unit_price if self.unit_price is not None else default.unit_price),
            shipping_cost=float(
                self.shipping_cost
                if self.shipping_cost is not None
                else (default.shipping_cost if default else 0.0)
            ),
            payment_terms_days=int(
                self.payment_terms_days
                if self.payment_terms_days is not None
                else (default.payment_terms_days if default else 30)
            ),
            delivery_days=int(
                self.delivery_days
                if self.delivery_days is not None
                else (default.delivery_days if default else 14)
            ),
            notes=self.raw_text or (default.notes if default else None),
        )


class GuardrailResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)
    adjusted_action: Optional[AgentAction] = None


class ScoringBreakdown(BaseModel):
    raw_scores: dict[str, float]
    weighted_scores: dict[str, float]
    total_utility: float
    meets_threshold: bool


class PostCallAnalysis(BaseModel):
    summary: str = ""
    outcome: str = "failed"
    key_patterns: list[str] = Field(default_factory=list)
    best_offer: Optional[dict[str, Any]] = None
    lessons: list[str] = Field(default_factory=list)

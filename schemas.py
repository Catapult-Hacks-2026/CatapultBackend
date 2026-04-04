from pydantic import BaseModel
from typing import Optional
from app.models.enums import Strategy


# ---------------------------------------------------------------------------
# Buyer configuration — what the purchasing company cares about
# ---------------------------------------------------------------------------

class BuyerConfig(BaseModel):
    """Weights and constraints set by the buyer for a negotiation."""
    target_unit_price: float
    max_unit_price: float               # hard ceiling — guardrail blocks above this
    target_shipping_cost: float = 0.0
    max_shipping_cost: float = 1000.0
    preferred_payment_terms: int = 60   # days (e.g., Net-60)
    min_payment_terms: int = 30
    preferred_delivery_days: int = 14
    max_delivery_days: int = 30
    quantity: int = 1000

    # Utility weights (must sum to 1.0)
    weight_price: float = 0.45
    weight_shipping: float = 0.15
    weight_payment_terms: float = 0.20
    weight_delivery: float = 0.20

    # Guardrail
    min_acceptable_utility: float = 0.6  # below this → reject or escalate


# ---------------------------------------------------------------------------
# Vendor offer — parsed from vendor communication
# ---------------------------------------------------------------------------

class VendorOffer(BaseModel):
    unit_price: float
    shipping_cost: float = 0.0
    payment_terms_days: int = 30        # Net-X
    delivery_days: int = 14
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

class CreateNegotiationRequest(BaseModel):
    vendor_name: str
    strategy: Strategy = Strategy.BALANCED
    config: BuyerConfig


class InboundVendorMessage(BaseModel):
    negotiation_id: str
    message: str
    offer: Optional[VendorOffer] = None  # if pre-parsed; otherwise LLM parses


class NegotiationResponse(BaseModel):
    id: str
    vendor_name: str
    status: str
    strategy: str
    round_number: int
    utility_score: Optional[float]
    current_offer: Optional[VendorOffer]


class AgentAction(BaseModel):
    """Structured output from the LLM orchestration pipeline."""
    counter_offer: Optional[VendorOffer] = None
    message: str                         # natural language response to vendor
    reasoning: str                       # internal — shown on dashboard
    should_accept: bool = False
    should_escalate: bool = False


class GuardrailResult(BaseModel):
    passed: bool
    violations: list[str] = []
    adjusted_action: Optional[AgentAction] = None


class ScoringBreakdown(BaseModel):
    raw_scores: dict[str, float]         # per-variable normalized scores
    weighted_scores: dict[str, float]    # after applying weights
    total_utility: float
    meets_threshold: bool

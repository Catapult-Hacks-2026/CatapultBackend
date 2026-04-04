from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.galileo.enums import (
    AgentLifecycleStatus,
    AgentStatus,
    BookingWindowStatus,
    EventStatus,
    NegotiationOutcome,
    PricePointType,
    ServiceType,
)


class GalileoBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Enterprise(GalileoBaseModel):
    id: str
    name: str
    description: str
    totalSavedHotels: float
    totalSavedAirlines: float
    totalSaved: float
    yoyChange: float
    hotelContractCount: int
    airlineContractCount: int


class Location(GalileoBaseModel):
    id: str
    companyId: str
    name: str
    address: str
    phone: str


class Company(GalileoBaseModel):
    id: str
    name: str
    initials: str
    description: str
    phone: str
    website: str
    industry: str
    badge: str
    locations: list[Location] = Field(default_factory=list)


class PricePoint(GalileoBaseModel):
    label: str
    price: float
    type: PricePointType
    round: int


class ActivityStreamItem(GalileoBaseModel):
    id: str
    agentId: str
    price: float
    badge: str | None = None
    badgeType: Literal["savings", "error", "neutral"]
    detail: str
    detailType: Literal["positive", "negative", "neutral"] | None = None
    timestamp: str
    active: bool


class Message(GalileoBaseModel):
    id: str
    agentId: str
    message: str
    sender: Literal["Galileo", "Rep"]
    timestamp: str


class PreviousNegotiation(GalileoBaseModel):
    id: str
    contractId: str
    region: str
    duration: str
    finalRate: float
    totalSavings: float
    status: Literal["ACTIVE", "ARCHIVED"]
    startDate: str
    endDate: str


class Agent(GalileoBaseModel):
    id: str
    enterpriseId: str
    eventId: str
    companyId: str
    companyName: str
    segment: str
    type: ServiceType
    status: AgentStatus
    lifecycleStatus: AgentLifecycleStatus
    outcome: NegotiationOutcome | None = None
    idealPrice: float
    ceilingPrice: float
    originalPrice: float
    currentPrice: float
    delta: float
    potentialSavings: float
    savingsToDate: float
    distanceToGoal: float
    isAccepted: bool
    pricePath: list[PricePoint] = Field(default_factory=list)
    activityStream: list[ActivityStreamItem] = Field(default_factory=list)
    transcript: list[Message] = Field(default_factory=list)
    previousNegotiations: list[PreviousNegotiation] = Field(default_factory=list)


class GalileoEvent(GalileoBaseModel):
    id: str
    enterpriseId: str
    name: str
    location: str
    startDate: str
    endDate: str
    attendees: int
    service: ServiceType
    status: EventStatus
    agents: list[Agent] = Field(default_factory=list)
    requirements: str | None = None
    budgetPerPerson: float | None = None
    winnerAgentId: str | None = None
    winnerTranscript: list[Message] | None = None
    winnerPricePath: list[PricePoint] | None = None


class PricingTrend(GalileoBaseModel):
    month: str
    year: int
    range: Literal["1Y", "ALL"]
    negotiatedPrice: float
    marketPrice: float


class BookingWindowEntry(GalileoBaseModel):
    month: str
    score: float
    status: BookingWindowStatus


class EnterpriseCompanyView(GalileoBaseModel):
    companyId: str
    enterpriseId: str
    locationId: str | None = None
    lifetimeSavings: float
    savingsDelta: float
    agreementsCount: int
    agreementsSummary: str
    avgDelta: float
    totalBookings: int
    totalSavings: float
    yoyChange: float
    pricingTrends: list[PricingTrend] = Field(default_factory=list)
    bookingWindow: list[BookingWindowEntry] = Field(default_factory=list)
    linkedEvents: list[GalileoEvent] = Field(default_factory=list)


class MarketPriceBand(GalileoBaseModel):
    marketPrice: float | None = None
    predictedWinPrice: float | None = None
    unit: str | None = None


class MarketPricingRequest(GalileoBaseModel):
    service: ServiceType
    location: str
    startDate: str
    endDate: str
    attendees: int


class MarketPricingResult(GalileoBaseModel):
    service: ServiceType
    hotel: MarketPriceBand | None = None
    airline: MarketPriceBand | None = None


class EventWindowPricing(GalileoBaseModel):
    marketCost: float | None = None
    negotiatedPrice: float | None = None
    savings: float | None = None


class EventWindowRequest(GalileoBaseModel):
    location: str
    eventType: str
    preferredTiming: str
    attendees: int
    nights: int
    eventDetails: str | None = None


class EventWindowResult(GalileoBaseModel):
    label: str
    startDate: str
    endDate: str
    explanation: str
    hotel: EventWindowPricing | None = None
    airline: EventWindowPricing | None = None
    negotiationConfidence: float


class AcceptOfferRequest(GalileoBaseModel):
    enterpriseId: str


class ServiceGuardrail(GalileoBaseModel):
    idealPrice: float
    ceilingPrice: float


class LaunchGuardrails(GalileoBaseModel):
    hotel: ServiceGuardrail | None = None
    airline: ServiceGuardrail | None = None


class LaunchNegotiationRequest(GalileoBaseModel):
    enterpriseId: str
    eventName: str
    service: ServiceType
    startDate: str
    endDate: str
    location: str
    attendees: int
    budgetPerPerson: float | None = None
    requirements: str | None = None
    guardrails: LaunchGuardrails | None = None


class InterventionResult(GalileoBaseModel):
    agentId: str
    status: str
    callRoutingInfo: str | None = None
    transferredAt: str


GalileoEvent.model_rebuild()
EnterpriseCompanyView.model_rebuild()

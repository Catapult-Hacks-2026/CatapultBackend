from enum import Enum


class AgentLifecycleStatus(str, Enum):
    INITIALIZING = "INITIALIZING"
    RINGING = "RINGING"
    ACTIVE = "ACTIVE"
    WRAPPING_UP = "WRAPPING_UP"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class NegotiationOutcome(str, Enum):
    RATE_CONFIRMED = "RATE_CONFIRMED"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"
    NO_AVAILABILITY = "NO_AVAILABILITY"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class AgentStatus(str, Enum):
    NEGOTIATING = "Negotiating"
    REVIEWING = "Reviewing"
    OPTIMIZED = "Optimized"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    QUEUED = "Queued"


class ServiceType(str, Enum):
    HOTEL = "Hotel"


class EventStatus(str, Enum):
    ACTIVE = "Active"
    COMPLETED = "Completed"


class PricePointType(str, Enum):
    OFFER = "offer"
    NEGOTIATED = "negotiated"
    CURRENT = "current"
    FINAL = "final"


class BookingWindowStatus(str, Enum):
    BEST_DEAL = "Best Deal"
    GOOD = "Good"
    PEAK = "Peak"

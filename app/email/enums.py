from enum import Enum


class EmailSessionStatus(str, Enum):
    INITIALIZING = "initializing"
    OUTREACH_PENDING = "outreach_pending"
    AWAITING_REPLY = "awaiting_reply"
    PROCESSING_REPLY = "processing_reply"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    FAILED = "failed"


class EmailOutcome(str, Enum):
    QUOTE_RECEIVED = "quote_received"
    NEEDS_HUMAN = "needs_human"
    BOOKING_READY = "booking_ready"
    RATE_CONFIRMED = "rate_confirmed"
    CLOSED = "closed"
    FAILED = "failed"


class EmailDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"

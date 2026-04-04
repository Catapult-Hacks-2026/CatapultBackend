from enum import Enum


class NegotiationStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    AWAITING_VENDOR = "awaiting_vendor"
    AWAITING_APPROVAL = "awaiting_approval"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class Strategy(str, Enum):
    AGGRESSIVE = "aggressive"       # "The Bulldog" — low anchoring, fast concession demands
    BALANCED = "balanced"           # "The Diplomat" — fair counter-offers, collaborative
    VOLUME = "volume"               # "The Bulk Buyer" — leverages volume for discounts
    RELATIONSHIP = "relationship"   # "The Partner" — optimizes long-term terms over price


class MessageRole(str, Enum):
    VENDOR = "vendor"
    AGENT = "agent"
    SYSTEM = "system"

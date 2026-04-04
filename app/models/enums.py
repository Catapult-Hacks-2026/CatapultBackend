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
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    VOLUME = "volume"
    RELATIONSHIP = "relationship"


class MessageRole(str, Enum):
    VENDOR = "vendor"
    AGENT = "agent"
    SYSTEM = "system"

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


class CampaignStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class CampaignJobStatus(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"
    DEFERRED = "deferred"


class WorkerEventType(str, Enum):
    CALL_STARTED = "call_started"
    QUOTE_RECEIVED = "quote_received"
    TRANSFERRED_TO_MANAGER = "transferred_to_manager"
    CALLBACK_REQUESTED = "callback_requested"
    DEAL_CLOSED = "deal_closed"
    FAILED = "failed"
    RETRY_RECOMMENDED = "retry_recommended"
    TERMINATED = "terminated"


class NegotiationAction(str, Enum):
    ASK_LOWER_RATE = "ask_lower_rate"
    WAIVE_FEE = "waive_fee"
    ESCALATE_MANAGER = "escalate_manager"
    MENTION_COMPETITOR = "mention_competitor"
    ACCEPT = "accept"
    CLOSE_POLITELY = "close_politely"

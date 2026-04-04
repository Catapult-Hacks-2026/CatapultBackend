from enum import Enum


class SessionStatus(str, Enum):
    INITIALIZING = "initializing"
    RINGING = "ringing"
    ACTIVE = "active"
    HOLD = "hold"
    WRAPPING_UP = "wrapping_up"
    COMPLETED = "completed"
    FAILED = "failed"


class NegotiationOutcome(str, Enum):
    RATE_CONFIRMED = "rate_confirmed"
    CALLBACK_REQUESTED = "callback_requested"
    NO_AVAILABILITY = "no_availability"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class MoveType(str, Enum):
    OPEN = "open"
    COUNTER = "counter"
    ACCEPT = "accept"
    REJECT = "reject"
    PROBE = "probe"
    CONCEDE = "concede"
    ANCHOR = "anchor"
    SILENCE = "silence"
    CLOSE = "close"

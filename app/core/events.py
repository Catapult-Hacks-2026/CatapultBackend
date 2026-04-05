from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    WORKER_STARTED = "worker_started"
    QUOTE_RECEIVED = "quote_received"
    DEAL_CLOSED = "deal_closed"
    WORKER_FAILED = "worker_failed"
    CALLBACK_REQUESTED = "callback_requested"
    WORKER_COMPLETED = "worker_completed"
    MARKET_UPDATE = "market_update"
    DUPLICATE_DETECTED = "duplicate_detected"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    CALL_ENDED = "call_ended"
    PRICE_CHANGED = "price_changed"
    DEAL_FINALIZED = "deal_finalized"


class WorkerEvent(BaseModel):
    event_type: EventType
    session_id: str
    campaign_id: str = ""
    hotel_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)


class EventBus:
    """In-process async pub/sub. For multi-process deployment swap to Redis Pub/Sub."""

    def __init__(self) -> None:
        # Each subscriber gets its own queue; optionally filtered by event_type set
        self._subscribers: list[tuple[asyncio.Queue[WorkerEvent], set[EventType] | None]] = []

    def subscribe(
        self,
        event_types: set[EventType] | None = None,
        maxsize: int = 256,
    ) -> asyncio.Queue[WorkerEvent]:
        """Return a queue that receives matching events. None = all event types."""
        q: asyncio.Queue[WorkerEvent] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append((q, event_types))
        return q

    def unsubscribe(self, q: asyncio.Queue[WorkerEvent]) -> None:
        self._subscribers = [(sq, f) for sq, f in self._subscribers if sq is not q]

    async def publish(self, event: WorkerEvent) -> None:
        for q, filter_types in self._subscribers:
            if filter_types is not None and event.event_type not in filter_types:
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "EventBus: subscriber queue full, dropping %s for session %s",
                    event.event_type,
                    event.session_id,
                )


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus

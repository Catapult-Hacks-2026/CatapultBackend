from __future__ import annotations

from typing import Any

from app.core.events import EventType, WorkerEvent, get_event_bus
from app.hotel.schemas import HotelQuote, WorkerSessionState


class WorkerEventEmitter:
    """Emits lifecycle events for a single worker session onto the shared EventBus."""

    def __init__(self, session_state: WorkerSessionState) -> None:
        self._session_id = session_state.session_id
        self._campaign_id = session_state.campaign_id
        self._hotel_id = session_state.hotel_target.hotel_id

    def _emit(self, event_type: EventType, payload: dict[str, Any] | None = None) -> None:
        event = WorkerEvent(
            event_type=event_type,
            session_id=self._session_id,
            campaign_id=self._campaign_id,
            hotel_id=self._hotel_id,
            payload=payload or {},
        )
        # Fire-and-forget: schedule on the running loop without awaiting
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(get_event_bus().publish(event))
        except RuntimeError:
            pass  # no running loop; skip emission (e.g. in tests)

    def worker_started(self, call_sid: str) -> None:
        self._emit(EventType.WORKER_STARTED, {"call_sid": call_sid})

    def quote_received(self, quote: HotelQuote) -> None:
        self._emit(EventType.QUOTE_RECEIVED, {
            "nightly_rate": quote.nightly_rate,
            "rate_type": quote.rate_type,
            "inclusions": quote.inclusions,
        })

    def deal_closed(self, nightly_rate: float, outcome: str) -> None:
        self._emit(EventType.DEAL_CLOSED, {
            "nightly_rate": nightly_rate,
            "outcome": outcome,
        })

    def worker_failed(self, reason: str) -> None:
        self._emit(EventType.WORKER_FAILED, {"reason": reason})

    def callback_requested(self) -> None:
        self._emit(EventType.CALLBACK_REQUESTED)

    def worker_completed(self, outcome: str, best_rate: float | None) -> None:
        self._emit(EventType.WORKER_COMPLETED, {
            "outcome": outcome,
            "best_rate": best_rate,
        })

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket
from langgraph.graph import END, StateGraph

from app.core.events import EventType, WorkerEvent, get_event_bus
from app.hotel.schemas import WorkerSessionState
from app.orchestration.worker_nodes import (
    acquire_lock_node,
    check_cross_session_node,
    check_terminate_node,
    decide_move_node,
    emit_memory_node,
    extract_facts_node,
    listen_node,
    load_context_node,
    load_memory_node,
    post_call_node,
    release_lock_node,
    route_lock,
    route_terminate,
    speak_node,
    start_voice_node,
    sync_quote_node,
)
from app.voice.pipeline import VoicePipeline
from app.voice.twilio_bridge import TwilioBridge

logger = logging.getLogger(__name__)

# Registry of active workers keyed by session_id
_active_workers: dict[str, "WorkerSession"] = {}
# Reverse lookup: galileo agent_id -> session_id
_agent_to_session: dict[str, str] = {}


def _galileo_agent_id(state: WorkerSessionState) -> str | None:
    value = state.hotel_target.market_context.get("galileo_agent_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


class WorkerSession:
    """Wraps a running worker graph instance and its VoicePipeline."""

    def __init__(self, initial_state: WorkerSessionState) -> None:
        self._state = initial_state
        self._pipeline: VoicePipeline | None = None
        self._pipeline_done = asyncio.Event()

    async def run(self) -> WorkerSessionState:
        graph = build_worker_graph()
        try:
            result = await graph.ainvoke(
                self._state,
                config={"configurable": {"thread_id": self._state.session_id}},
            )
            agent_id = _galileo_agent_id(result)
            if agent_id:
                try:
                    from app.galileo.database import finalize_agent_run

                    best_rate = min((q.nightly_rate for q in result.quotes_received), default=None)
                    await finalize_agent_run(
                        agent_id,
                        result.outcome.value if result.outcome else None,
                        best_rate=best_rate,
                    )
                except Exception:
                    logger.exception("Failed to finalize Galileo agent %s", agent_id)
            return result
        finally:
            unregister_worker(self._state.session_id)

    async def handle_media_stream_connected(self, websocket: WebSocket) -> None:
        bridge = TwilioBridge(websocket)
        self._pipeline = VoicePipeline(
            twilio_bridge=bridge,
            session_state=self._state,
            on_quote_received=self._on_quote_received,
            on_session_end=self._on_session_end,
            on_transcript_update=self._on_transcript_update,
        )
        await self._pipeline.start()

    async def wait_for_call_end(self) -> None:
        await self._pipeline_done.wait()

    def get_transcript_snapshot(self) -> list[dict[str, str]]:
        return list(self._state.transcript)

    async def _on_quote_received(self, quote: Any) -> None:
        agent_id = _galileo_agent_id(self._state)
        if agent_id:
            try:
                from app.galileo.database import record_agent_quote

                await record_agent_quote(agent_id, quote.nightly_rate, rate_type=quote.rate_type)
            except Exception:
                logger.exception("Failed to sync quote to Galileo agent %s", agent_id)

        # Record the price change in galileo DB and emit event
        galileo_agent_id = self._state.hotel_target.market_context.get("galileo_agent_id")
        if galileo_agent_id and hasattr(quote, "nightly_rate") and quote.nightly_rate > 0:
            try:
                from app.galileo.database import record_price_change
                await record_price_change(
                    agent_id=galileo_agent_id,
                    price=quote.nightly_rate,
                    source="hotel_rep",
                )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to record price change for agent %s", galileo_agent_id,
                )

            await get_event_bus().publish(WorkerEvent(
                event_type=EventType.PRICE_CHANGED,
                session_id=self._state.session_id,
                campaign_id=self._state.campaign_id,
                hotel_id=self._state.hotel_target.hotel_id,
                payload={
                    "galileo_agent_id": galileo_agent_id,
                    "price": quote.nightly_rate,
                    "source": "hotel_rep",
                },
            ))

    async def _on_session_end(self, state: WorkerSessionState) -> None:
        self._state = state
        event_id = state.hotel_target.market_context.get("event_id")
        dial_slot = state.hotel_target.market_context.get("dial_slot")
        if isinstance(event_id, str) and event_id:
            agent_id = _galileo_agent_id(state)
            if agent_id:
                try:
                    from app.galileo.database import promote_next_queued_agent
                    from app.galileo.router import trigger_twilio_call_for_agent_id

                    promoted_agent = await promote_next_queued_agent(event_id)
                    if promoted_agent is not None and promoted_agent["id"] != agent_id:
                        slot = dial_slot if isinstance(dial_slot, int) else 1
                        asyncio.create_task(trigger_twilio_call_for_agent_id(promoted_agent["id"], dial_slot=slot))
                except Exception:
                    logger.exception("Failed to start next queued Galileo agent after call end")
        self._pipeline_done.set()

    async def _on_transcript_update(self, data: dict[str, Any]) -> None:
        event_type_map = {
            "transcript_partial": EventType.TRANSCRIPT_PARTIAL,
            "transcript_final": EventType.TRANSCRIPT_FINAL,
            "call_ended": EventType.CALL_ENDED,
            "price_changed": EventType.PRICE_CHANGED,
        }
        mapped = event_type_map.get(data["type"])
        if mapped is None:
            return
        event = WorkerEvent(
            event_type=mapped,
            session_id=self._state.session_id,
            campaign_id=getattr(self._state, "campaign_id", ""),
            payload=data,
        )
        await get_event_bus().publish(event)
        if data["type"] == "transcript_final":
            agent_id = _galileo_agent_id(self._state)
            if agent_id:
                try:
                    from app.galileo.database import append_agent_message

                    sender = "Rep" if data.get("role") == "hotel" else "Galileo"
                    await append_agent_message(agent_id, str(data.get("content", "")), sender)
                except Exception:
                    logger.exception("Failed to sync transcript to Galileo agent %s", agent_id)


def build_worker_graph() -> Any:
    g = StateGraph(WorkerSessionState)

    g.add_node("load_context", load_context_node)
    g.add_node("load_memory", load_memory_node)
    g.add_node("acquire_lock", acquire_lock_node)
    g.add_node("start_voice", start_voice_node)
    g.add_node("listen", listen_node)
    g.add_node("extract_facts", extract_facts_node)
    g.add_node("sync_quote", sync_quote_node)
    g.add_node("check_cross_session", check_cross_session_node)
    g.add_node("decide_move", decide_move_node)
    g.add_node("speak", speak_node)
    g.add_node("check_terminate", check_terminate_node)
    g.add_node("post_call", post_call_node)
    g.add_node("emit_memory", emit_memory_node)
    g.add_node("release_lock", release_lock_node)

    g.set_entry_point("load_context")
    g.add_edge("load_context", "load_memory")
    g.add_edge("load_memory", "acquire_lock")
    g.add_conditional_edges("acquire_lock", route_lock, {
        "locked": END,
        "acquired": "start_voice",
    })
    g.add_edge("start_voice", "listen")
    g.add_edge("listen", "extract_facts")
    g.add_edge("extract_facts", "sync_quote")
    g.add_edge("sync_quote", "check_cross_session")
    g.add_edge("check_cross_session", "decide_move")
    g.add_edge("decide_move", "speak")
    g.add_edge("speak", "check_terminate")
    g.add_conditional_edges("check_terminate", route_terminate, {
        "continue": "listen",
        "done": "post_call",
    })
    g.add_edge("post_call", "emit_memory")
    g.add_edge("emit_memory", "release_lock")
    g.add_edge("release_lock", END)

    return g.compile()


def register_worker(session: WorkerSession) -> None:
    _active_workers[session._state.session_id] = session


def register_agent_mapping(agent_id: str, session_id: str) -> None:
    _agent_to_session[agent_id] = session_id


def get_active_worker(session_id: str) -> WorkerSession | None:
    return _active_workers.get(session_id)


def get_active_worker_for_agent(agent_id: str) -> tuple[str, WorkerSession] | None:
    session_id = _agent_to_session.get(agent_id)
    if session_id:
        worker = _active_workers.get(session_id)
        if worker:
            return session_id, worker
    return None


def unregister_worker(session_id: str) -> None:
    _active_workers.pop(session_id, None)
    # Clean up reverse mapping
    to_remove = [aid for aid, sid in _agent_to_session.items() if sid == session_id]
    for aid in to_remove:
        _agent_to_session.pop(aid, None)

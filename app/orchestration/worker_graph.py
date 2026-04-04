from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket
from langgraph.graph import END, StateGraph

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


class WorkerSession:
    """Wraps a running worker graph instance and its VoicePipeline."""

    def __init__(self, initial_state: WorkerSessionState) -> None:
        self._state = initial_state
        self._pipeline: VoicePipeline | None = None
        self._pipeline_connected = asyncio.Event()

    async def run(self) -> WorkerSessionState:
        graph = build_worker_graph()
        # The graph drives setup; VoicePipeline runs when the WebSocket connects
        result = await graph.ainvoke(
            self._state,
            config={"configurable": {"thread_id": self._state.session_id}},
        )
        return result

    async def handle_media_stream_connected(self, websocket: WebSocket) -> None:
        bridge = TwilioBridge(websocket)
        self._pipeline = VoicePipeline(
            twilio_bridge=bridge,
            session_state=self._state,
            on_quote_received=self._on_quote_received,
            on_session_end=self._on_session_end,
        )
        self._pipeline_connected.set()
        await self._pipeline.start()

    async def _on_quote_received(self, quote: Any) -> None:
        self._state.quotes_received.append(quote)

    async def _on_session_end(self, state: WorkerSessionState) -> None:
        self._state = state
        _active_workers.pop(state.session_id, None)


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


def get_active_worker(session_id: str) -> WorkerSession | None:
    return _active_workers.get(session_id)

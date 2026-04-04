from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover
    END = "__end__"
    StateGraph = None

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
from app.orchestration.worker_state import WorkerSessionState
from app.voice.pipeline import VoicePipeline
from app.voice.twilio_bridge import TwilioBridge

logger = logging.getLogger(__name__)

_active_workers: dict[str, "WorkerSession"] = {}


class WorkerSession:
    def __init__(self, initial_state: WorkerSessionState) -> None:
        self._state = dict(initial_state)
        self._pipeline: VoicePipeline | None = None
        self._pipeline_done = asyncio.Event()

    async def run(self) -> WorkerSessionState:
        logger.debug("Worker run started for %s", self._state["negotiation_id"])
        graph = build_worker_graph()
        try:
            result = await graph.ainvoke(self._state)
            self._state.update(result)
            return self._state
        finally:
            unregister_worker(self._state["negotiation_id"])

    async def handle_media_stream_connected(self, websocket: WebSocket) -> None:
        bridge = TwilioBridge(websocket)
        self._pipeline = VoicePipeline(
            twilio_bridge=bridge,
            session_state=self._state,
            on_quote_received=self._on_quote_received,
            on_session_end=self._on_session_end,
        )
        await self._pipeline.start()

    async def _on_quote_received(self, extracted_facts: Any) -> None:
        self._state["extracted_facts"] = extracted_facts.model_dump()
        offer = extracted_facts.to_vendor_offer()
        if offer is not None:
            self._state["latest_offer"] = offer.model_dump()

    async def _on_session_end(self, state: dict) -> None:
        self._state.update(state)
        self._pipeline_done.set()

    async def wait_for_call_end(self) -> None:
        await self._pipeline_done.wait()

    def snapshot(self) -> WorkerSessionState:
        return dict(self._state)


def build_worker_graph() -> Any:
    if StateGraph is None:
        return _FallbackCompiledGraph()
    graph = StateGraph(dict)
    graph.add_node("load_context", load_context_node)
    graph.add_node("load_memory", load_memory_node)
    graph.add_node("acquire_lock", acquire_lock_node)
    graph.add_node("start_voice", start_voice_node)
    graph.add_node("listen", listen_node)
    graph.add_node("extract_facts", extract_facts_node)
    graph.add_node("sync_quote", sync_quote_node)
    graph.add_node("check_cross_session", check_cross_session_node)
    graph.add_node("decide_move", decide_move_node)
    graph.add_node("speak", speak_node)
    graph.add_node("check_terminate", check_terminate_node)
    graph.add_node("post_call", post_call_node)
    graph.add_node("emit_memory", emit_memory_node)
    graph.add_node("release_lock", release_lock_node)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "load_memory")
    graph.add_edge("load_memory", "acquire_lock")
    graph.add_conditional_edges("acquire_lock", route_lock, {"locked": END, "acquired": "start_voice"})
    graph.add_edge("start_voice", "listen")
    graph.add_edge("listen", "extract_facts")
    graph.add_edge("extract_facts", "sync_quote")
    graph.add_edge("sync_quote", "check_cross_session")
    graph.add_edge("check_cross_session", "decide_move")
    graph.add_edge("decide_move", "speak")
    graph.add_edge("speak", "check_terminate")
    graph.add_conditional_edges("check_terminate", route_terminate, {"continue": "listen", "done": "post_call"})
    graph.add_edge("post_call", "emit_memory")
    graph.add_edge("emit_memory", "release_lock")
    graph.add_edge("release_lock", END)
    return graph.compile()


class _FallbackCompiledGraph:
    async def ainvoke(self, state: dict) -> dict:
        current = dict(state)
        for node in (load_context_node, load_memory_node, acquire_lock_node):
            current.update(await node(current))
        if route_lock(current) == "locked":
            return current
        current.update(await start_voice_node(current))
        current.update(await listen_node(current))
        current.update(await extract_facts_node(current))
        current.update(await sync_quote_node(current))
        current.update(await check_cross_session_node(current))
        current.update(await decide_move_node(current))
        current.update(await speak_node(current))
        current.update(await check_terminate_node(current))
        if route_terminate(current) == "done":
            current.update(await post_call_node(current))
            current.update(await emit_memory_node(current))
            current.update(await release_lock_node(current))
        return current


def register_worker(session: WorkerSession) -> None:
    _active_workers[session.snapshot()["negotiation_id"]] = session


def get_active_worker(negotiation_id: str) -> WorkerSession | None:
    return _active_workers.get(negotiation_id)


def unregister_worker(negotiation_id: str) -> None:
    _active_workers.pop(negotiation_id, None)

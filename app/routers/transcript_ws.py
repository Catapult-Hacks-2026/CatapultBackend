from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.events import EventType, get_event_bus
from app.orchestration.worker_graph import get_active_worker_for_agent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/agents/{agent_id}/transcript/ws")
async def transcript_ws(websocket: WebSocket, agent_id: str, last_index: int = -1) -> None:
    # Worker may not be registered yet if the call was just launched.
    # Poll briefly before giving up.
    result = None
    for _ in range(15):
        result = get_active_worker_for_agent(agent_id)
        if result:
            break
        await asyncio.sleep(1)

    if not result:
        await websocket.accept()
        await websocket.send_json({"type": "error", "message": "No active call"})
        await websocket.close(code=4004)
        return

    session_id, worker = result
    await websocket.accept()

    # Backfill existing transcript entries
    snapshot = worker.get_transcript_snapshot()
    backfill_entries = [
        {"role": entry["role"], "content": entry["content"], "index": i}
        for i, entry in enumerate(snapshot)
        if i > last_index
    ]
    if backfill_entries:
        await websocket.send_json({
            "type": "backfill",
            "entries": backfill_entries,
            "session_id": session_id,
        })

    # Subscribe to transcript events for this session
    queue = get_event_bus().subscribe(
        event_types={
            EventType.TRANSCRIPT_PARTIAL,
            EventType.TRANSCRIPT_FINAL,
            EventType.CALL_ENDED,
            EventType.PRICE_CHANGED,
            EventType.DEAL_FINALIZED,
        },
    )

    try:
        await _pump(websocket, queue, session_id, agent_id)
    except WebSocketDisconnect:
        logger.info("Transcript WS disconnected for agent %s", agent_id)
    except Exception:
        logger.exception("Transcript WS error for agent %s", agent_id)
    finally:
        get_event_bus().unsubscribe(queue)


async def _pump(
    websocket: WebSocket,
    queue: asyncio.Queue,
    session_id: str,
    agent_id: str = "",
) -> None:
    receive_task = asyncio.create_task(websocket.receive_text())
    queue_task = asyncio.create_task(queue.get())

    try:
        while True:
            done, _ = await asyncio.wait(
                {receive_task, queue_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if receive_task in done:
                try:
                    raw = receive_task.result()
                except WebSocketDisconnect:
                    raise
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    msg = {}
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                receive_task = asyncio.create_task(websocket.receive_text())

            if queue_task in done:
                event = queue_task.result()
                # Forward events that match this session or this agent
                is_session_match = event.session_id == session_id
                is_agent_match = (
                    event.event_type in (EventType.PRICE_CHANGED, EventType.DEAL_FINALIZED)
                    and event.payload.get("galileo_agent_id") == agent_id
                )
                if not is_session_match and not is_agent_match:
                    queue_task = asyncio.create_task(queue.get())
                    continue

                payload = dict(event.payload)
                payload["type"] = event.event_type.value
                payload["session_id"] = session_id
                payload["timestamp"] = datetime.now(timezone.utc).isoformat()
                await websocket.send_json(payload)

                if event.event_type == EventType.CALL_ENDED:
                    break
                queue_task = asyncio.create_task(queue.get())
    finally:
        receive_task.cancel()
        queue_task.cancel()

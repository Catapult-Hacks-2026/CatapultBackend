from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.events import EventType, get_event_bus
from app.galileo.database import get_agent as db_get_agent
from app.orchestration.worker_graph import get_active_worker_for_agent

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/agents/{agent_id}/transcript/ws")
async def transcript_ws(websocket: WebSocket, agent_id: str, last_index: int = -1) -> None:
    await websocket.accept()
    result = get_active_worker_for_agent(agent_id)
    if not result:
        await _poll_transcript_from_db(websocket, agent_id, last_index)
        return

    session_id, worker = result

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


@router.websocket("/agents/{agent_id}/activity-stream/ws")
async def activity_stream_ws(websocket: WebSocket, agent_id: str) -> None:
    await websocket.accept()
    await _poll_activity_from_db(websocket, agent_id)


def _role_from_sender(sender: str) -> str:
    return "hotel" if sender == "Rep" else "agent"


async def _receive_or_timeout(websocket: WebSocket, timeout_seconds: float = 1.0) -> dict:
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout_seconds)
    except TimeoutError:
        return {}
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


async def _poll_transcript_from_db(websocket: WebSocket, agent_id: str, last_index: int) -> None:
    sent_index = last_index
    call_ended_sent = False

    try:
        while True:
            payload = await db_get_agent(agent_id)
            if not payload:
                await websocket.send_json({"type": "error", "message": "Agent not found"})
                await websocket.close(code=4004)
                return

            transcript = payload.get("transcript") or []
            pending_entries = [
                {
                    "role": _role_from_sender(str(entry.get("sender", ""))),
                    "content": str(entry.get("message", "")),
                    "index": i,
                }
                for i, entry in enumerate(transcript)
                if i > sent_index
            ]
            if pending_entries:
                message_type = "backfill" if sent_index == last_index else "transcript_final"
                if message_type == "backfill":
                    await websocket.send_json({
                        "type": "backfill",
                        "entries": pending_entries,
                        "session_id": "",
                    })
                else:
                    for entry in pending_entries:
                        await websocket.send_json({
                            "type": "transcript_final",
                            "role": entry["role"],
                            "content": entry["content"],
                            "index": entry["index"],
                            "session_id": "",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        })
                sent_index = pending_entries[-1]["index"]

            status = str(payload.get("status", ""))
            if status not in {"Negotiating", "Reviewing"} and not call_ended_sent:
                await websocket.send_json({
                    "type": "call_ended",
                    "session_id": "",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "outcome": payload.get("outcome"),
                })
                call_ended_sent = True
                return

            msg = await _receive_or_timeout(websocket)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        logger.info("Transcript DB WS disconnected for agent %s", agent_id)
    except Exception:
        logger.exception("Transcript DB WS error for agent %s", agent_id)


async def _poll_activity_from_db(websocket: WebSocket, agent_id: str) -> None:
    sent_ids: set[str] = set()

    try:
        while True:
            payload = await db_get_agent(agent_id)
            if not payload:
                await websocket.send_json({"type": "error", "message": "Agent not found"})
                await websocket.close(code=4004)
                return

            activities = payload.get("activityStream") or []
            new_items = [item for item in activities if str(item.get("id", "")) not in sent_ids]
            if new_items:
                if not sent_ids:
                    await websocket.send_json({
                        "type": "backfill",
                        "entries": new_items,
                        "agent_id": agent_id,
                    })
                else:
                    for item in new_items:
                        await websocket.send_json({
                            "type": "activity",
                            "agent_id": agent_id,
                            "entry": item,
                        })
                sent_ids.update(str(item.get("id", "")) for item in new_items)

            msg = await _receive_or_timeout(websocket)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        logger.info("Activity WS disconnected for agent %s", agent_id)
    except Exception:
        logger.exception("Activity WS error for agent %s", agent_id)


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

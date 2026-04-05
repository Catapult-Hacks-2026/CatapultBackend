from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

_session_updates: dict[str, dict] = {}


@router.patch("/sessions/{session_id}")
async def patch_session(session_id: str, payload: dict) -> dict:
    current = dict(_session_updates.get(session_id, {}))
    current.update(payload)
    _session_updates[session_id] = current
    return {"session_id": session_id, "status": "updated"}

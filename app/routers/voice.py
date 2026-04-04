import asyncio
import uuid

from fastapi import APIRouter, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from app.hotel.schemas import HotelTarget, WorkerSessionState
from app.orchestration.worker_graph import WorkerSession, get_active_worker, register_worker

router = APIRouter()


class CallRequest(BaseModel):
    phone_number: str
    hotel_id: str = "test-hotel"
    target_rate: float = 150.0
    max_rate: float = 200.0
    check_in: str = "2026-05-01"
    check_out: str = "2026-05-03"
    room_type: str = "standard"


@router.post("/call")
async def place_call(req: CallRequest) -> dict:
    session_id = str(uuid.uuid4())
    target = HotelTarget(
        hotel_id=req.hotel_id,
        phone_number=req.phone_number,
        check_in=req.check_in,
        check_out=req.check_out,
        room_type=req.room_type,
        target_rate=req.target_rate,
        max_rate=req.max_rate,
    )
    state = WorkerSessionState(
        session_id=session_id,
        campaign_id="manual-test",
        hotel_target=target,
    )
    session = WorkerSession(state)
    register_worker(session)
    asyncio.create_task(session.run(), name=f"worker-{session_id}")
    return {"session_id": session_id, "status": "calling", "to": req.phone_number}


@router.post("/twilio-stream/{session_id}")
def twilio_stream_webhook(session_id: str) -> Response:
    from app.core.config import get_settings
    host = get_settings().base_url.removeprefix("https://").removeprefix("http://")
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{host}/voice/media-stream/{session_id}"/>
  </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@router.websocket("/media-stream/{session_id}")
async def media_stream(websocket: WebSocket, session_id: str) -> None:
    worker = get_active_worker(session_id)
    if not worker:
        await websocket.close(code=4004)
        return
    await websocket.accept()
    await worker.handle_media_stream_connected(websocket)

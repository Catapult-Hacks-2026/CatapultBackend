import logging

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import Response

from app.core.config import get_settings
from app.orchestration.worker_graph import get_active_worker

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/twilio-stream/{session_id}")
def twilio_stream_webhook(session_id: str) -> Response:
    base_url = get_settings().base_url.rstrip("/")
    stream_url = f"{base_url.replace('https://', 'wss://').replace('http://', 'ws://')}/voice/media-stream/{session_id}"
    logger.info("twilio_stream_webhook: session_id=%s stream_url=%s", session_id, stream_url)
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{stream_url}"/>
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


@router.post("/status/{session_id}")
async def twilio_status_callback(session_id: str, request: Request) -> dict:
    form = await request.form()
    payload = dict(form)
    logger.info(
        "twilio_status: session_id=%s call_sid=%s call_status=%s to=%s from=%s direction=%s answered_by=%s",
        session_id,
        payload.get("CallSid"),
        payload.get("CallStatus"),
        payload.get("To"),
        payload.get("From"),
        payload.get("Direction"),
        payload.get("AnsweredBy"),
    )
    return {"ok": True}

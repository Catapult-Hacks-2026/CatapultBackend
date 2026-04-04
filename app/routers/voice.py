from fastapi import APIRouter, WebSocket
from fastapi.responses import Response

from app.orchestration.worker_graph import get_active_worker

router = APIRouter()


@router.post("/twilio-stream/{session_id}")
def twilio_stream_webhook(session_id: str) -> Response:
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://{{host}}/voice/media-stream/{session_id}"/>
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

from fastapi import APIRouter, WebSocket
from fastapi.responses import Response

from app.core.database import get_db
from app.services.voice import build_twiml_response, handle_media_stream, initiate_call

router = APIRouter()


@router.post("/call/{negotiation_id}")
def start_call(negotiation_id: str, payload: dict) -> dict:
    return initiate_call(negotiation_id, payload["vendor_phone_number"])


@router.post("/twilio-stream/{negotiation_id}")
def twilio_stream_webhook(negotiation_id: str) -> Response:
    return Response(content=build_twiml_response(negotiation_id), media_type="application/xml")


@router.websocket("/media-stream/{negotiation_id}")
async def media_stream(websocket: WebSocket, negotiation_id: str) -> None:
    await handle_media_stream(websocket, negotiation_id)


@router.get("/calls/{negotiation_id}")
def get_call_status(negotiation_id: str) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM call_sessions WHERE negotiation_id = ? ORDER BY created_at DESC LIMIT 1",
        (negotiation_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return {"status": "not_found", "transcript": ""}
    return {
        "id": row["id"],
        "twilio_call_sid": row["twilio_call_sid"],
        "status": row["status"],
        "transcript": row["transcript"],
        "created_at": row["created_at"],
    }

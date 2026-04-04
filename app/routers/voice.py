import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import Response

from app.core.database import get_db
from app.orchestration.worker_graph import WorkerSession, get_active_worker, register_worker
from app.orchestration.worker_state import build_worker_state
from app.services.voice import build_twiml_response, handle_media_stream

router = APIRouter()


@router.post("/worker/{negotiation_id}/start")
async def start_worker_call(negotiation_id: str, payload: dict) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT vendor_name, product_category, config FROM negotiations WHERE id = ?",
        (negotiation_id,),
    ).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Negotiation not found")

    worker = get_active_worker(negotiation_id)
    if worker is None:
        vendor_phone = payload.get("vendor_phone") or payload.get("vendor_phone_number")
        if not vendor_phone:
            raise HTTPException(status_code=400, detail="vendor_phone is required to start a worker call")
        worker = WorkerSession(
            build_worker_state(
                negotiation_id=negotiation_id,
                vendor_name=row["vendor_name"],
                vendor_phone=vendor_phone,
                product_category=row["product_category"],
                buyer_config=json.loads(row["config"]),
                campaign_id=payload.get("campaign_id"),
            )
        )
        register_worker(worker)
        asyncio.create_task(worker.run())
    return {"negotiation_id": negotiation_id, "worker_registered": True}


@router.post("/twilio-stream/{negotiation_id}")
def twilio_stream_webhook(negotiation_id: str) -> Response:
    return Response(content=build_twiml_response(negotiation_id), media_type="application/xml")


@router.websocket("/media-stream/{negotiation_id}")
async def media_stream(websocket: WebSocket, negotiation_id: str) -> None:
    worker = get_active_worker(negotiation_id)
    if worker is not None:
        await websocket.accept()
        await worker.handle_media_stream_connected(websocket)
        return
    await handle_media_stream(websocket, negotiation_id)


@router.get("/calls/{negotiation_id}")
def get_call_sessions(negotiation_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, twilio_call_sid, status, transcript, created_at
        FROM call_sessions
        WHERE negotiation_id = ?
        ORDER BY created_at DESC
        """,
        (negotiation_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "twilio_call_sid": row["twilio_call_sid"],
            "status": row["status"],
            "transcript": row["transcript"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.email.mailgun_provider import MailgunProvider
from app.email.schemas import EmailTarget
from app.email.session_registry import get_email_session_registry
from app.email.worker import (
    build_email_session_from_target,
    extract_session_id_from_recipient,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_running_email_tasks: dict[str, asyncio.Task] = {}


class StartEmailSessionRequest(BaseModel):
    campaign_id: str = ""
    target: EmailTarget


@router.post("/sessions/start")
async def start_email_session(request: StartEmailSessionRequest) -> dict:
    session_id = str(uuid.uuid4())
    worker = await build_email_session_from_target(session_id, request.campaign_id, request.target)
    task = asyncio.create_task(worker.run(), name=f"email-session-{session_id}")
    _running_email_tasks[session_id] = task
    return {"session_id": session_id, "status": "started", "channel": "email"}


@router.get("/sessions/{session_id}/status")
async def get_email_session_status(session_id: str) -> dict:
    task = _running_email_tasks.get(session_id)
    worker = await get_email_session_registry().get_worker(session_id)
    if task is None and worker is None:
        raise HTTPException(status_code=404, detail="Email session not found")
    if task is not None and task.done():
        exc = task.exception()
        if exc:
            return {"session_id": session_id, "status": "failed", "error": str(exc)}
    state = worker.state if worker is not None else None
    return {
        "session_id": session_id,
        "status": state.status.value if state else "completed",
        "outcome": state.outcome.value if state and state.outcome else None,
    }


@router.post("/webhooks/mailgun")
async def mailgun_webhook(request: Request) -> dict:
    form = await request.form()
    form_data = {key: str(value) for key, value in form.items()}

    provider = MailgunProvider()
    if not await provider.validate_webhook(form_data):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    message = await provider.normalize_inbound(form_data)
    session_id = extract_session_id_from_recipient(message.to_address)
    worker = await get_email_session_registry().get_worker(session_id)
    if worker is None:
        logger.warning("No active email worker for recipient %s", message.to_address)
        raise HTTPException(status_code=404, detail="No active email session found")

    await worker.handle_inbound_email(message)
    return {"status": "processed", "session_id": session_id}

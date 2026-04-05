from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.artifacts.schemas import NegotiatedRateAgreement
from app.core.config import get_settings
from app.email.contract_artifacts import (
    build_receipt_email_body,
    build_receipt_email_subject,
    persist_contract_artifacts,
)
from app.email.schemas import EmailAttachment, OutboundEmail
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


class TestNegotiationReportRequest(BaseModel):
    recipient: str
    outcome: str = "rate_confirmed"
    report: NegotiatedRateAgreement | None = None


def _default_test_report() -> NegotiatedRateAgreement:
    return NegotiatedRateAgreement.model_validate({
        "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
        "galileoReferenceId": "GAL-8492-ORD",
        "parties": {
            "clientName": "Acme Travel",
            "vendorName": "Ord Hotel",
        },
        "term": {
            "startDate": "2026-05-01",
            "endDate": "2026-05-03",
        },
        "rateMatrix": [
            {
                "roomOrFareType": "King",
                "negotiatedRateUSD": 189,
                "discountFromBAR": "12%",
            }
        ],
        "criticalClauses": {
            "inventoryGuarantee": "NLRA (Non-Last Room Availability)",
            "blackoutDates": ["None"],
            "cancellationPolicy": "48 hours prior",
        },
        "concessions": [
            "Breakfast included",
            "Complimentary Wi-Fi",
        ],
        "billingAndSettlement": {
            "method": "Transient - Employee Corporate Card",
        },
    })


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
            return {"session_id": session_id, "status": "failed", "channel": "email", "error": str(exc)}
    state = worker.state if worker is not None else (task.result() if task is not None and task.done() else None)
    if state is None and task is not None and not task.done():
        return {"session_id": session_id, "status": "running", "channel": "email"}
    return {
        "session_id": session_id,
        "channel": "email",
        "status": state.status.value if state else "completed",
        "outcome": state.outcome.value if state and state.outcome else None,
        "subject": state.subject if state else None,
        "reply_address": state.reply_address if state else None,
        "escalation_reason": state.escalation_reason if state else None,
        "contract_details": state.contract_details.model_dump() if state and state.contract_details else None,
        "receipt_artifacts": state.receipt_artifacts.model_dump() if state and state.receipt_artifacts else None,
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
    return {"status": "processed", "session_id": session_id, "channel": "email"}


@router.post("/reports/test")
async def send_test_negotiation_report(request: TestNegotiationReportRequest) -> dict:
    report = request.report or _default_test_report()
    session_id = str(uuid.uuid4())
    artifacts = persist_contract_artifacts(session_id, report, outcome=request.outcome)
    pdf_file = Path(artifacts.pdf_path)

    outbound = OutboundEmail(
        to_address=request.recipient,
        subject=build_receipt_email_subject(report, request.outcome),
        text=build_receipt_email_body(report, outcome=request.outcome),
        metadata={
            "session_id": session_id,
            "channel": "voice",
            "artifact_type": "negotiation_report",
        },
        attachments=[
            EmailAttachment(
                filename=pdf_file.name,
                content_type="application/pdf",
                data=pdf_file.read_bytes(),
            )
        ],
    )
    receipt = await MailgunProvider().send_message(outbound)
    return {
        "session_id": session_id,
        "status": "sent",
        "channel": "email",
        "recipient": request.recipient,
        "subject": outbound.subject,
        "from_address": get_settings().email_from_address,
        "provider_message_id": receipt.provider_message_id,
        "json_path": artifacts.json_path,
        "pdf_path": artifacts.pdf_path,
        "report": report.model_dump(),
    }

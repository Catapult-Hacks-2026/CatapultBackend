from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.artifacts.schemas import NegotiatedRateAgreement
from app.core.config import get_settings
from app.email.contract_artifacts import (
    build_receipt_email_body,
    build_receipt_email_subject,
    persist_contract_artifacts,
)
from app.email.mailgun_provider import MailgunProvider
from app.email.schemas import OutboundEmail
from app.artifacts.schemas import EmailAttachment

router = APIRouter()
logger = logging.getLogger(__name__)


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


@router.post("/reports/test")
async def send_test_negotiation_report(request: TestNegotiationReportRequest) -> dict:
    report = request.report or _default_test_report()
    session_id = str(uuid.uuid4())
    logger.info(
        "email.reports.test: session_id=%s recipient=%s outcome=%s report_ref=%s",
        session_id,
        request.recipient,
        request.outcome,
        report.galileoReferenceId,
    )
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
    logger.info(
        "email.reports.test: sending recipient=%s subject=%s from=%s pdf_path=%s",
        request.recipient,
        outbound.subject,
        get_settings().email_from_address,
        artifacts.pdf_path,
    )
    receipt = await MailgunProvider().send_message(outbound)
    logger.info(
        "email.reports.test: sent session_id=%s provider_message_id=%s",
        session_id,
        receipt.provider_message_id,
    )
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

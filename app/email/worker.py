from __future__ import annotations

import asyncio
import logging
from email.utils import parseaddr
from pathlib import Path

from app.core.events import EventType, WorkerEvent, get_event_bus
from app.core.shared_clients import get_http_client
from app.core.urls import build_upstream_url
from app.core.config import get_settings
from app.email.contract_artifacts import (
    build_contract_artifacts,
    build_receipt_email_body,
    build_receipt_email_subject,
    resolve_receipt_recipient,
)
from app.email.enums import EmailDirection, EmailOutcome, EmailSessionStatus
from app.email.guardrails import validate_email_turn
from app.email.mailgun_provider import MailgunProvider
from app.email.parser import build_default_subject
from app.email.schemas import (
    EmailAttachment,
    EmailMessage,
    EmailSessionState,
    EmailTarget,
    OutboundEmail,
)
from app.email.session_registry import get_email_session_registry
from app.llm.email_fact_extractor import extract_facts_from_email
from app.llm.email_negotiation import decide_email_move, generate_email_reply
from app.llm.post_email_analyzer import analyze_email_thread
from app.memory.behavioral_store import get_behavioral_store
from app.memory.memory_candidates import extract_memory_candidates_from_transcript

logger = logging.getLogger(__name__)
def _provider_for_settings() -> MailgunProvider:
    provider = get_settings().email_provider.lower()
    if provider != "mailgun":
        raise ValueError(f"Unsupported email provider: {provider}")
    return MailgunProvider()


def build_reply_address(session_id: str) -> str:
    settings = get_settings()
    domain = settings.email_reply_domain or settings.email_from_address.split("@")[-1]
    return f"{session_id}@{domain}"


class EmailWorkerSession:
    def __init__(self, initial_state: EmailSessionState) -> None:
        self._state = initial_state
        self._provider = _provider_for_settings()
        self._done = asyncio.Event()
        self._lock = asyncio.Lock()

    async def run(self) -> EmailSessionState:
        registry = get_email_session_registry()
        await registry.register_worker(self._state.session_id, self)
        try:
            await self._load_context()
            await self._send_initial_outreach()
            await self._done.wait()
            await self._finalize()
            return self._state
        finally:
            await registry.unregister_worker(self._state.session_id)

    async def handle_inbound_email(self, message: EmailMessage) -> None:
        async with self._lock:
            registry = get_email_session_registry()
            if not await registry.mark_processed(message.provider_message_id or message.message_id):
                return

            self._state.status = EmailSessionStatus.PROCESSING_REPLY
            self._state.last_inbound_message = message
            self._state.messages.append(message)
            self._state.transcript.append({"role": "hotel", "content": message.text})
            await self._sync_message(message, session_status=self._state.status.value)

            extraction = await extract_facts_from_email(message.text, self._state)
            if extraction.quote is not None:
                self._state.quotes_received.append(extraction.quote)
                await self._sync_quote(extraction.quote)
                await get_event_bus().publish(WorkerEvent(
                    event_type=EventType.QUOTE_RECEIVED,
                    session_id=self._state.session_id,
                    campaign_id=self._state.campaign_id,
                    hotel_id=self._state.email_target.hotel_id,
                    payload={
                        "channel": "email",
                        "nightly_rate": extraction.quote.nightly_rate,
                        "rate_type": extraction.quote.rate_type,
                    },
                ))

            move = await decide_email_move(self._state, extraction)
            guardrail = validate_email_turn(self._state, message, extraction, move)
            if not guardrail.allow_auto_send:
                self._state.status = EmailSessionStatus.ESCALATED
                self._state.needs_human_review = True
                self._state.escalation_reason = guardrail.reason
                if guardrail.terminal_outcome == EmailOutcome.BOOKING_READY.value:
                    self._state.outcome = EmailOutcome.BOOKING_READY
                else:
                    self._state.outcome = EmailOutcome.NEEDS_HUMAN
                await self._sync_escalation(message)
                self._done.set()
                return

            reply_text = await generate_email_reply(move, self._state, extraction)
            move.response_text = reply_text
            self._state.moves_made.append(move)
            self._state.transcript.append({"role": "agent", "content": reply_text})

            outbound = await self._send_reply(reply_text, message)
            self._state.last_outbound_message = outbound
            self._state.messages.append(outbound)
            await self._sync_message(outbound, session_status=EmailSessionStatus.AWAITING_REPLY.value)

            if move.should_terminate:
                self._state.outcome = (
                    EmailOutcome.RATE_CONFIRMED if move.move_type.value == "accept" else EmailOutcome.CLOSED
                )
                self._state.status = EmailSessionStatus.COMPLETED
                self._done.set()
            else:
                self._state.status = EmailSessionStatus.AWAITING_REPLY
                if extraction.quote is not None and self._state.outcome is None:
                    self._state.outcome = EmailOutcome.QUOTE_RECEIVED

    def signal_done(self) -> None:
        self._done.set()

    @property
    def state(self) -> EmailSessionState:
        return self._state

    async def _load_context(self) -> None:
        hotel_id = self._state.email_target.hotel_id
        client = get_http_client()
        try:
            hotel_resp = await client.get(build_upstream_url(f"/api/hotels/{hotel_id}"))
            hotel_data = hotel_resp.json() if hotel_resp.status_code == 200 else {}
        except Exception as exc:
            logger.warning("email load_context: failed to fetch hotel %s: %s", hotel_id, exc)
            hotel_data = {}

        store = get_behavioral_store()
        profile = await store.load_priors(hotel_id)
        self._state.behavioral_priors = {
            "hotel_name": hotel_data.get("name", hotel_id),
            "negotiation_summary": profile.to_prompt_context(),
        }

    async def _send_initial_outreach(self) -> None:
        self._state.status = EmailSessionStatus.OUTREACH_PENDING
        self._state.reply_address = build_reply_address(self._state.session_id)
        self._state.subject = self._state.subject or build_default_subject(
            self._state.email_target.check_in,
            self._state.email_target.check_out,
            self._state.email_target.room_type,
        )

        opening_body = await generate_email_reply(
            await decide_email_move(self._state, self._empty_extraction()),
            self._state,
            self._empty_extraction(),
        )
        outbound = await self._send_outbound_message(opening_body)
        self._state.last_outbound_message = outbound
        self._state.messages.append(outbound)
        self._state.transcript.append({"role": "agent", "content": opening_body})
        self._state.status = EmailSessionStatus.AWAITING_REPLY
        await self._sync_session()
        await self._sync_message(outbound, session_status=self._state.status.value)
        await get_event_bus().publish(WorkerEvent(
            event_type=EventType.WORKER_STARTED,
            session_id=self._state.session_id,
            campaign_id=self._state.campaign_id,
            hotel_id=self._state.email_target.hotel_id,
            payload={"channel": "email", "subject": self._state.subject},
        ))

    async def _send_reply(self, body: str, inbound: EmailMessage) -> EmailMessage:
        outbound = OutboundEmail(
            to_address=self._state.email_target.email_address,
            subject=self._state.subject or inbound.subject,
            text=body,
            reply_to=self._state.reply_address,
            in_reply_to=inbound.message_id or inbound.provider_message_id,
            references=[*inbound.references, inbound.message_id or inbound.provider_message_id],
            metadata={"session_id": self._state.session_id, "channel": "email"},
        )
        receipt = await self._provider.send_message(outbound)
        return EmailMessage(
            provider=receipt.provider,
            direction=EmailDirection.OUTBOUND,
            subject=outbound.subject,
            text=body,
            from_address=get_settings().email_from_address,
            to_address=outbound.to_address,
            provider_message_id=receipt.provider_message_id,
            message_id=receipt.message_id,
            in_reply_to=outbound.in_reply_to,
            references=outbound.references,
            metadata=receipt.metadata,
        )

    async def _send_outbound_message(self, body: str) -> EmailMessage:
        outbound = OutboundEmail(
            to_address=self._state.email_target.email_address,
            subject=self._state.subject,
            text=body,
            reply_to=self._state.reply_address,
            metadata={"session_id": self._state.session_id, "channel": "email"},
        )
        receipt = await self._provider.send_message(outbound)
        return EmailMessage(
            provider=receipt.provider,
            direction=EmailDirection.OUTBOUND,
            subject=outbound.subject,
            text=body,
            from_address=get_settings().email_from_address,
            to_address=outbound.to_address,
            provider_message_id=receipt.provider_message_id,
            message_id=receipt.message_id,
            metadata=receipt.metadata,
        )

    async def _finalize(self) -> None:
        if self._state.outcome is None:
            self._state.outcome = EmailOutcome.FAILED
        if self._state.status not in (EmailSessionStatus.ESCALATED, EmailSessionStatus.FAILED):
            self._state.status = EmailSessionStatus.COMPLETED

        analysis = await analyze_email_thread(self._state)
        if self._state.outcome is None:
            self._state.outcome = analysis.outcome
        receipt_recipient = resolve_receipt_recipient(self._state)
        if receipt_recipient:
            try:
                artifacts = await build_contract_artifacts(self._state)
                await self._send_receipt_email(receipt_recipient, artifacts.pdf_path)
                if self._state.receipt_artifacts is not None:
                    self._state.receipt_artifacts.email_sent_to = receipt_recipient
            except Exception as exc:
                logger.warning("email receipt send failed for %s: %s", self._state.session_id, exc)
                self._state.error_log.append(f"receipt_send_failed: {exc}")

        await self._sync_session()
        store = get_behavioral_store()
        await store.store_session_summary(
            session_id=self._state.session_id,
            hotel_id=self._state.email_target.hotel_id,
            summary=analysis.summary,
            outcome=analysis.outcome,
            quotes=self._state.quotes_received,
        )
        features = await extract_memory_candidates_from_transcript(
            session_id=self._state.session_id,
            hotel_id=self._state.email_target.hotel_id,
            transcript=self._state.transcript,
        )
        if features:
            await store.store_features(features)

        event_type = EventType.WORKER_COMPLETED if self._state.outcome != EmailOutcome.FAILED else EventType.WORKER_FAILED
        await get_event_bus().publish(WorkerEvent(
            event_type=event_type,
            session_id=self._state.session_id,
            campaign_id=self._state.campaign_id,
            hotel_id=self._state.email_target.hotel_id,
            payload={"channel": "email", "outcome": self._state.outcome.value},
        ))

    async def _send_receipt_email(self, recipient: str, pdf_path: str) -> None:
        if self._state.contract_details is None:
            return

        pdf_file = Path(pdf_path)
        subject = build_receipt_email_subject(
            self._state.contract_details,
            self._state.outcome.value if self._state.outcome else None,
        )
        outbound = OutboundEmail(
            to_address=recipient,
            subject=subject,
            text=build_receipt_email_body(
                self._state.contract_details,
                session_state=self._state,
                outcome=self._state.outcome.value if self._state.outcome else None,
            ),
            metadata={
                "session_id": self._state.session_id,
                "channel": "email",
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
        await self._provider.send_message(outbound)

    async def _sync_session(self) -> None:
        payload = {
            "session_id": self._state.session_id,
            "campaign_id": self._state.campaign_id,
            "hotel_id": self._state.email_target.hotel_id,
            "channel": "email",
            "status": self._state.status.value,
            "outcome": self._state.outcome.value if self._state.outcome else None,
            "subject": self._state.subject,
            "reply_address": self._state.reply_address,
            "escalation_reason": self._state.escalation_reason,
            "contract_details": self._state.contract_details.model_dump() if self._state.contract_details else None,
            "receipt_artifacts": self._state.receipt_artifacts.model_dump() if self._state.receipt_artifacts else None,
        }
        try:
            await get_http_client().post(build_upstream_url("/api/email/sessions/"), json=payload)
        except Exception as exc:
            logger.warning("email sync_session failed for %s: %s", self._state.session_id, exc)

    async def _sync_message(self, message: EmailMessage, session_status: str) -> None:
        payload = {
            "session_id": self._state.session_id,
            "hotel_id": self._state.email_target.hotel_id,
            "channel": "email",
            "direction": message.direction.value,
            "subject": message.subject,
            "text": message.text,
            "provider": message.provider,
            "provider_message_id": message.provider_message_id,
            "message_id": message.message_id,
            "in_reply_to": message.in_reply_to,
            "references": message.references,
            "from_address": message.from_address,
            "to_address": message.to_address,
            "attachment_count": message.attachment_count,
            "status": session_status,
        }
        try:
            await get_http_client().post(build_upstream_url("/api/email/messages/"), json=payload)
        except Exception as exc:
            logger.warning("email sync_message failed for %s: %s", self._state.session_id, exc)

    async def _sync_quote(self, quote) -> None:
        payload = {
            "session_id": self._state.session_id,
            "hotel_id": self._state.email_target.hotel_id,
            "channel": "email",
            "nightly_rate": quote.nightly_rate,
            "total_rate": quote.total_rate,
            "inclusions": quote.inclusions,
            "cancellation_policy": quote.cancellation_policy,
            "rate_type": quote.rate_type,
            "fees": quote.fees,
        }
        try:
            await get_http_client().post(build_upstream_url("/api/quotes/"), json=payload)
        except Exception as exc:
            logger.warning("email sync_quote failed for %s: %s", self._state.session_id, exc)

    async def _sync_escalation(self, inbound: EmailMessage) -> None:
        await self._sync_session()
        payload = {
            "session_id": self._state.session_id,
            "hotel_id": self._state.email_target.hotel_id,
            "channel": "email",
            "subject": inbound.subject,
            "reason": self._state.escalation_reason,
            "latest_message": inbound.text,
        }
        try:
            await get_http_client().post(build_upstream_url("/api/email/escalations/"), json=payload)
        except Exception as exc:
            logger.warning("email sync_escalation failed for %s: %s", self._state.session_id, exc)

    def _empty_extraction(self):
        from app.email.schemas import EmailExtractionResult
        return EmailExtractionResult()


async def build_email_session_from_target(
    session_id: str,
    campaign_id: str,
    target: EmailTarget,
) -> EmailWorkerSession:
    subject_prefix = target.campaign_metadata.get("subject")
    state = EmailSessionState(
        session_id=session_id,
        campaign_id=campaign_id,
        email_target=target,
        subject=subject_prefix or build_default_subject(target.check_in, target.check_out, target.room_type),
    )
    return EmailWorkerSession(state)


def extract_session_id_from_recipient(recipient: str) -> str:
    _, address = parseaddr(recipient)
    local_part = address.split("@")[0]
    return local_part.strip().lower()

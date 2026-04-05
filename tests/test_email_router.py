import unittest
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.artifacts.schemas import NegotiatedRateAgreement
from app.email.enums import EmailDirection, EmailOutcome, EmailSessionStatus
from app.email.schemas import EmailMessage, EmailSessionState, EmailTarget, SentEmailReceipt
from app.routers.email import _running_email_tasks, router


class _RegistryStub:
    def __init__(self, worker) -> None:
        self._worker = worker

    async def get_worker(self, _: str):
        return self._worker


class EmailRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        _running_email_tasks.clear()
        app = FastAPI()
        app.include_router(router, prefix="/email")
        self.client = TestClient(app)
        self.target = EmailTarget(
            hotel_id="hotel-1",
            email_address="sales@example.com",
            contact_name="Jordan",
            check_in="2026-05-01",
            check_out="2026-05-03",
            room_type="King",
            target_rate=180.0,
            max_rate=220.0,
        )

    def tearDown(self) -> None:
        _running_email_tasks.clear()
        self.client.close()

    def test_start_email_session_returns_started_payload(self) -> None:
        worker = Mock()
        worker.run = AsyncMock()
        fake_task = Mock()
        fake_task.done.return_value = False

        def fake_create_task(coro, name=None):
            self.assertEqual(name, "email-session-session-123")
            coro.close()
            return fake_task

        with patch("app.routers.email.uuid.uuid4", return_value="session-123"):
            with patch("app.routers.email.build_email_session_from_target", AsyncMock(return_value=worker)):
                with patch("app.routers.email.asyncio.create_task", side_effect=fake_create_task):
                    response = self.client.post(
                        "/email/sessions/start",
                        json={"campaign_id": "camp-1", "target": self.target.model_dump()},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"session_id": "session-123", "status": "started", "channel": "email"},
        )
        self.assertIs(_running_email_tasks["session-123"], fake_task)

    def test_status_returns_running_when_worker_has_not_registered_yet(self) -> None:
        task = Mock()
        task.done.return_value = False
        _running_email_tasks["session-1"] = task

        with patch("app.routers.email.get_email_session_registry", return_value=_RegistryStub(None)):
            response = self.client.get("/email/sessions/session-1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"session_id": "session-1", "status": "running", "channel": "email"},
        )

    def test_status_returns_state_details_for_active_worker(self) -> None:
        state = EmailSessionState(
            session_id="session-1",
            email_target=self.target,
            status=EmailSessionStatus.AWAITING_REPLY,
            outcome=EmailOutcome.QUOTE_RECEIVED,
            subject="Hotel rate request",
            reply_address="session-1@reply.example.com",
        )
        worker = Mock(state=state)

        with patch("app.routers.email.get_email_session_registry", return_value=_RegistryStub(worker)):
            response = self.client.get("/email/sessions/session-1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "awaiting_reply")
        self.assertEqual(response.json()["outcome"], "quote_received")
        self.assertEqual(response.json()["subject"], "Hotel rate request")
        self.assertEqual(response.json()["reply_address"], "session-1@reply.example.com")
        self.assertEqual(response.json()["channel"], "email")

    def test_status_returns_failed_error_when_task_crashes(self) -> None:
        task = Mock()
        task.done.return_value = True
        task.exception.return_value = RuntimeError("mailgun timeout")
        _running_email_tasks["session-1"] = task

        with patch("app.routers.email.get_email_session_registry", return_value=_RegistryStub(None)):
            response = self.client.get("/email/sessions/session-1/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(response.json()["channel"], "email")
        self.assertIn("mailgun timeout", response.json()["error"])

    def test_mailgun_webhook_processes_inbound_message(self) -> None:
        provider = Mock()
        provider.validate_webhook = AsyncMock(return_value=True)
        message = EmailMessage(
            provider="mailgun",
            direction=EmailDirection.INBOUND,
            subject="Quote",
            text="We can offer 195.",
            from_address="sales@example.com",
            to_address="session-1@reply.example.com",
            message_id="<msg-1>",
        )
        provider.normalize_inbound = AsyncMock(return_value=message)
        worker = Mock()
        worker.handle_inbound_email = AsyncMock()

        with patch("app.routers.email.MailgunProvider", return_value=provider):
            with patch("app.routers.email.get_email_session_registry", return_value=_RegistryStub(worker)):
                response = self.client.post(
                    "/email/webhooks/mailgun",
                    data={
                        "timestamp": "1710000000",
                        "token": "token-123",
                        "signature": "valid-signature",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "processed", "session_id": "session-1", "channel": "email"},
        )
        provider.validate_webhook.assert_awaited_once()
        provider.normalize_inbound.assert_awaited_once()
        worker.handle_inbound_email.assert_awaited_once_with(message)

    def test_send_test_negotiation_report_returns_report_payload(self) -> None:
        receipt = SentEmailReceipt(
            provider="mailgun",
            provider_message_id="<mailgun-id>",
            message_id="<mailgun-id>",
        )
        provider = Mock()
        provider.send_message = AsyncMock(return_value=receipt)
        report = NegotiatedRateAgreement.model_validate({
            "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
            "galileoReferenceId": "GAL-1234-ORD",
            "parties": {"clientName": "Acme Travel", "vendorName": "Ord Hotel"},
            "term": {"startDate": "2026-05-01", "endDate": "2026-05-03"},
            "rateMatrix": [{"roomOrFareType": "King", "negotiatedRateUSD": 189, "discountFromBAR": "12%"}],
            "criticalClauses": {
                "inventoryGuarantee": "NLRA (Non-Last Room Availability)",
                "blackoutDates": ["None"],
                "cancellationPolicy": "48 hours prior",
            },
            "concessions": ["Breakfast included"],
            "billingAndSettlement": {"method": "Transient - Employee Corporate Card"},
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("app.routers.email.persist_contract_artifacts") as persist_artifacts:
                persist_artifacts.return_value.json_path = str(Path(temp_dir) / "report.json")
                persist_artifacts.return_value.pdf_path = str(Path(temp_dir) / "report.pdf")
                Path(persist_artifacts.return_value.pdf_path).write_bytes(b"%PDF-1.4\n")
                with patch("app.routers.email.MailgunProvider", return_value=provider):
                    with patch("app.routers.email.get_settings") as mock_settings:
                        mock_settings.return_value.email_from_address = "bot@example.com"
                        response = self.client.post(
                            "/email/reports/test",
                            json={
                                "recipient": "agent@example.com",
                                "outcome": "rate_confirmed",
                                "report": report.model_dump(),
                            },
                        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "sent")
        self.assertEqual(payload["recipient"], "agent@example.com")
        self.assertEqual(payload["subject"], "Galileo negotiation report | rate_confirmed | GAL-1234-ORD")
        self.assertEqual(payload["report"]["galileoReferenceId"], "GAL-1234-ORD")
        provider.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.email import router


class EmailReportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/email")
        self.client = TestClient(app)

    def test_reports_test_returns_provider_details(self) -> None:
        with patch("app.routers.email.persist_contract_artifacts") as persist_artifacts:
            persist_artifacts.return_value.json_path = "/tmp/report.json"
            persist_artifacts.return_value.pdf_path = "/tmp/report.pdf"
            with patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4\n"):
                with patch("app.routers.email.MailgunProvider") as provider_cls:
                    provider_cls.return_value.send_message = AsyncMock()
                    provider_cls.return_value.send_message.return_value.provider_message_id = "<mailgun-id>"
                    with patch("app.routers.email.get_settings") as settings:
                        settings.return_value.email_from_address = "bot@example.com"
                        response = self.client.post(
                            "/email/reports/test",
                            json={"recipient": "agent@example.com", "outcome": "rate_confirmed"},
                        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "sent")
        self.assertEqual(payload["channel"], "email")
        self.assertEqual(payload["recipient"], "agent@example.com")
        self.assertEqual(payload["from_address"], "bot@example.com")
        self.assertEqual(payload["provider_message_id"], "<mailgun-id>")


if __name__ == "__main__":
    unittest.main()

import hashlib
import hmac
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.artifacts.schemas import EmailAttachment
from app.email.mailgun_provider import MailgunProvider
from app.email.schemas import OutboundEmail


class MailgunProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_includes_attachments(self) -> None:
        mock_response = Mock()
        mock_response.json.return_value = {"id": "<mailgun-id>"}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.email.mailgun_provider.get_settings") as mock_settings:
            mock_settings.return_value.email_webhook_secret = "secret"
            mock_settings.return_value.email_reply_domain = "reply.example.com"
            mock_settings.return_value.email_from_address = "bot@example.com"
            mock_settings.return_value.email_api_key = "key-test"
            with patch("app.email.mailgun_provider.get_http_client", return_value=mock_client):
                provider = MailgunProvider()
                await provider.send_message(OutboundEmail(
                    to_address="agent@example.com",
                    subject="Contract receipt",
                    text="Attached.",
                    attachments=[EmailAttachment(filename="receipt.pdf", content_type="application/pdf", data=b"pdf-bytes")],
                ))

        _, kwargs = mock_client.post.call_args
        self.assertIn("files", kwargs)
        self.assertEqual(kwargs["files"][0][0], "attachment")
        self.assertEqual(kwargs["files"][0][1][0], "receipt.pdf")

    async def test_send_message_filters_blank_references_and_keeps_message_id(self) -> None:
        mock_response = Mock()
        mock_response.json.return_value = {"Message-Id": "<message-id>"}
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.email.mailgun_provider.get_settings") as mock_settings:
            mock_settings.return_value.email_reply_domain = "reply.example.com"
            mock_settings.return_value.email_from_address = "bot@example.com"
            mock_settings.return_value.email_api_key = "key-test"
            with patch("app.email.mailgun_provider.get_http_client", return_value=mock_client):
                provider = MailgunProvider()
                receipt = await provider.send_message(OutboundEmail(
                    to_address="agent@example.com",
                    subject="Follow-up",
                    text="Checking in.",
                    references=["<ref-1>", "", "<ref-2>"],
                ))

        _, kwargs = mock_client.post.call_args
        self.assertEqual(kwargs["data"]["h:References"], "<ref-1> <ref-2>")
        self.assertEqual(receipt.provider_message_id, "<message-id>")
        self.assertEqual(receipt.message_id, "<message-id>")

    async def test_validate_webhook_signature(self) -> None:
        timestamp = "1710000000"
        token = "token-123"
        secret = "secret-456"
        signature = hmac.new(
            secret.encode("utf-8"),
            msg=f"{timestamp}{token}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()

        with patch("app.email.mailgun_provider.get_settings") as mock_settings:
            mock_settings.return_value.email_webhook_secret = secret
            provider = MailgunProvider()
            is_valid = await provider.validate_webhook(
                {"timestamp": timestamp, "token": token, "signature": signature}
            )

        self.assertTrue(is_valid)

    async def test_normalize_inbound_uses_latest_reply_and_headers(self) -> None:
        provider = MailgunProvider()
        message = await provider.normalize_inbound({
            "subject": "Hotel quote",
            "body-plain": "Latest reply\n\nOn Tue, someone wrote:\n> older text",
            "sender": "sales@example.com",
            "recipient": "session-1@reply.example.com",
            "Message-Id": "<provider-id>",
            "attachment-count": "0",
            "message-headers": '[["Message-Id","<message-id>"],["In-Reply-To","<prev>"],["References","<ref-1> <ref-2>"]]',
        })

        self.assertEqual(message.text, "Latest reply")
        self.assertEqual(message.message_id, "<message-id>")
        self.assertEqual(message.in_reply_to, "<prev>")
        self.assertEqual(message.references, ["<ref-1>", "<ref-2>"])


if __name__ == "__main__":
    unittest.main()

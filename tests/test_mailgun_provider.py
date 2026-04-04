import hashlib
import hmac
import unittest
from unittest.mock import patch

from app.email.mailgun_provider import MailgunProvider


class MailgunProviderTests(unittest.IsolatedAsyncioTestCase):
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

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from email.utils import getaddresses

from app.core.config import get_settings
from app.core.shared_clients import get_http_client
from app.email.enums import EmailDirection
from app.email.parser import extract_latest_reply
from app.email.provider import EmailProviderAdapter
from app.email.schemas import EmailMessage, OutboundEmail, SentEmailReceipt

logger = logging.getLogger(__name__)


class MailgunProvider(EmailProviderAdapter):
    provider_name = "mailgun"

    async def send_message(self, message: OutboundEmail) -> SentEmailReceipt:
        settings = get_settings()
        domain = settings.email_reply_domain
        if not domain:
            raise ValueError("EMAIL_REPLY_DOMAIN must be configured for Mailgun")
        if not settings.email_from_address:
            raise ValueError("EMAIL_FROM_ADDRESS must be configured for Mailgun")
        if not settings.email_api_key:
            raise ValueError("EMAIL_API_KEY must be configured for Mailgun")

        logger.info(
            "mailgun.send_message: to=%s subject=%s attachments=%d reply_to=%s has_refs=%s domain=%s from=%s",
            message.to_address,
            message.subject,
            len(message.attachments),
            bool(message.reply_to),
            bool(message.references),
            domain,
            settings.email_from_address,
        )
        logger.debug(
            "mailgun.send_message: metadata_keys=%s",
            sorted(message.metadata.keys()),
        )

        payload = {
            "from": settings.email_from_address,
            "to": message.to_address,
            "subject": message.subject,
            "text": message.text,
        }
        if message.reply_to:
            payload["h:Reply-To"] = message.reply_to
        if message.in_reply_to:
            payload["h:In-Reply-To"] = message.in_reply_to
        references = [reference for reference in message.references if reference]
        if references:
            payload["h:References"] = " ".join(references)
        for key, value in message.metadata.items():
            payload[f"v:{key}"] = value

        files = [
            ("attachment", (attachment.filename, attachment.data, attachment.content_type))
            for attachment in message.attachments
        ]

        response = await get_http_client().post(
            f"https://api.mailgun.net/v3/{domain}/messages",
            auth=("api", settings.email_api_key),
            data=payload,
            files=files or None,
        )
        logger.info(
            "mailgun.send_message: response_status=%s to=%s subject=%s",
            response.status_code,
            message.to_address,
            message.subject,
        )
        response.raise_for_status()
        data = response.json()
        provider_message_id = data.get("id") or data.get("message-id") or data.get("Message-Id", "")
        message_id = data.get("Message-Id") or data.get("message-id") or provider_message_id
        logger.info(
            "mailgun.send_message: provider_message_id=%s message_id=%s",
            provider_message_id,
            message_id,
        )
        return SentEmailReceipt(
            provider=self.provider_name,
            provider_message_id=provider_message_id,
            message_id=message_id,
            metadata=data,
        )

    async def validate_webhook(self, form_data: dict[str, str]) -> bool:
        settings = get_settings()
        signature = form_data.get("signature", "")
        timestamp = form_data.get("timestamp", "")
        token = form_data.get("token", "")
        if not (settings.email_webhook_secret and signature and timestamp and token):
            return False

        digest = hmac.new(
            settings.email_webhook_secret.encode("utf-8"),
            msg=f"{timestamp}{token}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(digest, signature)

    async def normalize_inbound(self, form_data: dict[str, str]) -> EmailMessage:
        headers_raw = form_data.get("message-headers", "[]")
        try:
            header_pairs = json.loads(headers_raw)
        except json.JSONDecodeError:
            header_pairs = []
        headers = {key: value for key, value in header_pairs}

        plain_text = form_data.get("stripped-text") or form_data.get("body-plain") or ""
        latest_reply = extract_latest_reply(plain_text)
        from_address = form_data.get("sender") or headers.get("From", "")
        recipients = getaddresses([form_data.get("recipient", "")])
        to_address = recipients[0][1] if recipients else form_data.get("recipient", "")

        references = headers.get("References", "").split()
        return EmailMessage(
            provider=self.provider_name,
            direction=EmailDirection.INBOUND,
            subject=form_data.get("subject", ""),
            text=latest_reply or plain_text.strip(),
            from_address=from_address,
            to_address=to_address,
            provider_message_id=form_data.get("Message-Id", "") or headers.get("Message-Id", ""),
            message_id=headers.get("Message-Id", form_data.get("Message-Id", "")),
            in_reply_to=headers.get("In-Reply-To", ""),
            references=references,
            attachment_count=int(form_data.get("attachment-count", "0") or "0"),
            metadata={
                "sender": from_address,
                "recipient": to_address,
            },
        )

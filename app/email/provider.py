from __future__ import annotations

from abc import ABC, abstractmethod

from app.email.schemas import EmailMessage, OutboundEmail, SentEmailReceipt


class EmailProviderAdapter(ABC):
    provider_name: str

    @abstractmethod
    async def send_message(self, message: OutboundEmail) -> SentEmailReceipt:
        raise NotImplementedError

    @abstractmethod
    async def validate_webhook(self, form_data: dict[str, str]) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def normalize_inbound(self, form_data: dict[str, str]) -> EmailMessage:
        raise NotImplementedError

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

try:
    from anthropic import Anthropic, AsyncAnthropic
except ImportError:  # pragma: no cover
    Anthropic = None
    AsyncAnthropic = None

from app.core.config import get_settings

_sync_client: Anthropic | None = None
_async_client: AsyncAnthropic | None = None


def get_anthropic_client() -> Anthropic:
    global _sync_client
    settings = get_settings()
    if Anthropic is None:
        raise RuntimeError("anthropic package is not installed")
    if not settings.anthropic_api_key:
        raise RuntimeError("Anthropic API key is not configured")
    if _sync_client is None:
        _sync_client = Anthropic(api_key=settings.anthropic_api_key)
    return _sync_client


def _get_async_anthropic_client() -> AsyncAnthropic:
    global _async_client
    settings = get_settings()
    if AsyncAnthropic is None:
        raise RuntimeError("anthropic package is not installed")
    if not settings.anthropic_api_key:
        raise RuntimeError("Anthropic API key is not configured")
    if _async_client is None:
        _async_client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _async_client


def _extract_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def invoke_json(
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> dict:
    settings = get_settings()
    response = get_anthropic_client().messages.create(
        model=model or settings.negotiation_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = _extract_text(response)
    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        stricter = f"{user}\n\nReturn valid JSON only. Do not add markdown."
        retry = get_anthropic_client().messages.create(
            model=model or settings.negotiation_model,
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": stricter}],
        )
        return _extract_json(_extract_text(retry))


async def async_invoke_json(
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> dict:
    return await asyncio.to_thread(
        invoke_json,
        system,
        user,
        model,
        max_tokens,
        temperature,
    )


async def stream_text(
    system: str,
    user: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    stream = _get_async_anthropic_client().messages.stream(
        model=model or settings.negotiation_model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    async with stream as result:
        async for text in result.text_stream:
            if text:
                yield text

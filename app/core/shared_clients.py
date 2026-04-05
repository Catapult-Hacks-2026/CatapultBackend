from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import get_settings

if TYPE_CHECKING:
    import openai

_http_client: httpx.AsyncClient | None = None
_openai_client: Any | None = None


def get_http_client() -> httpx.AsyncClient:
    """Shared AsyncClient with connection pooling for all backend API calls."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"ngrok-skip-browser-warning": "true"},
        )
    return _http_client


def get_openai_client() -> "openai.AsyncOpenAI":
    """Shared AsyncOpenAI client across all workers."""
    global _openai_client
    if _openai_client is None:
        import openai

        _openai_client = openai.AsyncOpenAI(api_key=get_settings().openai_api_key)
    return _openai_client


async def close_shared_clients() -> None:
    """Call on app shutdown to drain connections cleanly."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None

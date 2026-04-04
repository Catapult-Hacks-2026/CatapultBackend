from __future__ import annotations

from app.core.config import get_settings


def _normalize(base_url: str) -> str:
    return base_url.rstrip("/")


def build_public_url(path: str) -> str:
    settings = get_settings()
    base = settings.public_base_url or settings.base_url
    return f"{_normalize(base)}{path}"


def build_upstream_url(path: str) -> str:
    settings = get_settings()
    base = settings.upstream_api_base_url or settings.base_url
    return f"{_normalize(base)}{path}"

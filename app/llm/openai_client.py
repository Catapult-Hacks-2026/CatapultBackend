from __future__ import annotations

import json
from typing import AsyncGenerator

from app.core.shared_clients import get_openai_client
from app.core.config import get_settings


async def invoke_json(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> dict:
    settings = get_settings()
    response = await get_openai_client().chat.completions.create(
        model=model or settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return json.loads(response.choices[0].message.content)


async def invoke_text(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.4,
) -> str:
    settings = get_settings()
    response = await get_openai_client().chat.completions.create(
        model=model or settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return response.choices[0].message.content or ""


async def stream_text(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    stream = await get_openai_client().chat.completions.create(
        model=model or settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta

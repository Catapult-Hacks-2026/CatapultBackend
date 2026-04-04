from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WORD_BOOST = [
    "unit price",
    "shipping",
    "payment terms",
    "net 30",
    "net 60",
    "bulk discount",
    "delivery lead time",
    "moq",
]


class DeepgramRealtimeSTT:
    def __init__(
        self,
        on_partial: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        on_final: Callable[[str, float, list], Coroutine[Any, Any, None]] | None = None,
        on_error: Callable[[Exception], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.on_partial = on_partial
        self.on_final = on_final
        self.on_error = on_error
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        settings = get_settings()
        query = urlencode(
            {
                "encoding": "mulaw",
                "sample_rate": 8000,
                "channels": 1,
                "interim_results": "true",
                "endpointing": settings.deepgram_endpointing_ms,
                "model": settings.deepgram_model,
                "smart_format": "true",
                "keywords": ",".join(_WORD_BOOST),
            }
        )
        self._ws = await websockets.connect(
            f"wss://api.deepgram.com/v1/listen?{query}",
            additional_headers={"Authorization": f"Token {settings.deepgram_api_key}"},
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("Deepgram STT connected")

    async def send_audio(self, audio_bytes: bytes) -> None:
        if self._ws is None:
            return
        await self._ws.send(audio_bytes)

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                if event.get("type") != "Results":
                    continue
                channel = event.get("channel", {})
                alternatives = channel.get("alternatives", [])
                if not alternatives:
                    continue
                alt = alternatives[0]
                text = (alt.get("transcript") or "").strip()
                if not text:
                    continue
                confidence = float(alt.get("confidence") or 0.0)
                words = alt.get("words") or []
                if event.get("is_final"):
                    if self.on_final:
                        await self.on_final(text, confidence, words)
                elif self.on_partial:
                    await self.on_partial(text)
        except ConnectionClosed:
            logger.info("Deepgram WebSocket closed")
        except Exception as exc:
            logger.exception("Deepgram receive loop error")
            if self.on_error:
                await self.on_error(exc)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task is not None:
            self._receive_task.cancel()
        self._ws = None
        logger.info("Deepgram STT closed")

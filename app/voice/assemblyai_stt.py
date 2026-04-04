from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_WORD_BOOST = [
    "rate", "nightly", "per night", "check-in", "check-out",
    "cancellation", "refundable", "corporate", "negotiated",
    "breakfast included", "complimentary", "availability",
]


class AssemblyAIRealtimeSTT:
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
        silence_ms = settings.assemblyai_end_utterance_silence_ms
        url = (
            f"wss://api.assemblyai.com/v2/realtime/ws"
            f"?sample_rate=8000"
            f"&token={settings.assemblyai_api_key}"
            f"&encoding=pcm_mulaw"
            f"&end_utterance_silence_threshold={silence_ms}"
        )
        self._ws = await websockets.connect(url)

        # Send word boost config
        await self._ws.send(json.dumps({
            "word_boost": _WORD_BOOST,
            "boost_param": "high",
        }))

        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("AssemblyAI STT connected")

    async def send_audio(self, audio_bytes: bytes) -> None:
        if self._ws is None:
            return
        encoded = base64.b64encode(audio_bytes).decode("utf-8")
        await self._ws.send(json.dumps({"audio_data": encoded}))

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                msg_type = event.get("message_type")

                if msg_type == "PartialTranscript":
                    text = event.get("text", "")
                    if text and self.on_partial:
                        await self.on_partial(text)

                elif msg_type == "FinalTranscript":
                    text = event.get("text", "")
                    confidence = event.get("confidence", 1.0)
                    words = event.get("words", [])
                    if text and self.on_final:
                        await self.on_final(text, confidence, words)

                elif msg_type == "SessionBegins":
                    logger.debug("AssemblyAI session began: %s", event.get("session_id"))

                elif msg_type == "Error":
                    err = RuntimeError(f"AssemblyAI error: {event.get('error')}")
                    if self.on_error:
                        await self.on_error(err)

        except ConnectionClosed:
            logger.info("AssemblyAI WebSocket closed")
        except Exception as exc:
            logger.exception("AssemblyAI receive loop error")
            if self.on_error:
                await self.on_error(exc)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"terminate_session": True}))
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task is not None:
            self._receive_task.cancel()
        self._ws = None
        logger.info("AssemblyAI STT closed")

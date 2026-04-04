from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_MIN_AUDIO_CHUNK_MS = 60
_SAMPLE_RATE = 8000
_BYTES_PER_SAMPLE = 1  # pcm_mulaw at 8 kHz is 1 byte per sample
_MIN_AUDIO_CHUNK_BYTES = (_SAMPLE_RATE * _MIN_AUDIO_CHUNK_MS // 1000) * _BYTES_PER_SAMPLE

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
        self._audio_buffer = bytearray()

    async def connect(self) -> None:
        settings = get_settings()
        silence_ms = settings.assemblyai_end_utterance_silence_ms
        url = (
            f"wss://streaming.assemblyai.com/v3/ws"
            f"?sample_rate=8000"
            f"&speech_model=universal-streaming-english"
            f"&encoding=pcm_mulaw"
            f"&format_turns=false"
            f"&min_turn_silence={silence_ms}"
            f"&max_turn_silence={silence_ms}"
        )
        self._ws = await websockets.connect(
            url,
            extra_headers={"Authorization": settings.assemblyai_api_key},
        )

        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("AssemblyAI STT connected")

    async def send_audio(self, audio_bytes: bytes) -> None:
        if self._ws is None:
            return
        self._audio_buffer.extend(audio_bytes)
        while len(self._audio_buffer) >= _MIN_AUDIO_CHUNK_BYTES:
            chunk = bytes(self._audio_buffer[:_MIN_AUDIO_CHUNK_BYTES])
            del self._audio_buffer[:_MIN_AUDIO_CHUNK_BYTES]
            await self._ws.send(chunk)

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                msg_type = event.get("type")

                if msg_type == "Turn":
                    text = event.get("transcript", "")
                    end_of_turn = event.get("end_of_turn", False)
                    confidence = event.get("end_of_turn_confidence", 1.0)
                    words = event.get("words", [])
                    if text and self.on_partial:
                        await self.on_partial(text)
                    if text and end_of_turn and self.on_final:
                        await self.on_final(text, confidence, words)

                elif msg_type == "Begin":
                    logger.debug("AssemblyAI session began: %s", event.get("id"))

                elif msg_type == "Error":
                    err = RuntimeError(f"AssemblyAI error: {event.get('error')}")
                    if self.on_error:
                        await self.on_error(err)

                elif msg_type == "Termination":
                    logger.info("AssemblyAI session terminated")
                    break

        except ConnectionClosed:
            logger.info("AssemblyAI WebSocket closed")
        except Exception as exc:
            logger.exception("AssemblyAI receive loop error")
            if self.on_error:
                await self.on_error(exc)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                if len(self._audio_buffer) >= _MIN_AUDIO_CHUNK_BYTES:
                    await self._ws.send(bytes(self._audio_buffer))
                await self._ws.send(json.dumps({"type": "Terminate"}))
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task is not None:
            self._receive_task.cancel()
        self._ws = None
        self._audio_buffer.clear()
        logger.info("AssemblyAI STT closed")

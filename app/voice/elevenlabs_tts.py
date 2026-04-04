from __future__ import annotations

import asyncio
import base64
import json
import logging

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ElevenLabsStreamingTTS:
    def __init__(self) -> None:
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None

    def _build_url(self) -> str:
        settings = get_settings()
        voice_id = settings.tts_voice_id
        model_id = settings.elevenlabs_model_id
        return (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
            f"?model_id={model_id}&output_format=ulaw_8000"
            f"&optimize_streaming_latency=3"
        )

    async def _open_connection(self) -> None:
        settings = get_settings()
        self._ws = await websockets.connect(
            self._build_url(),
            extra_headers={"xi-api-key": settings.tts_api_key},
        )
        await self._ws.send(json.dumps({
            "text": " ",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "use_speaker_boost": False,
            },
            "xi_api_key": settings.tts_api_key,
        }))
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("ElevenLabs TTS connected")

    async def connect(self) -> None:
        # Clean up any prior connection so this is safe to call per-utterance
        if self._ws is not None:
            await self.close()
        self._audio_queue = asyncio.Queue()
        await self._open_connection()

    async def send_text_chunk(self, text: str, flush: bool = False) -> None:
        if self._ws is None:
            return
        payload: dict = {"text": text}
        if flush:
            payload["flush"] = True
        try:
            await self._ws.send(json.dumps(payload))
        except ConnectionClosed:
            logger.warning("ElevenLabs WS closed while sending text chunk")

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                if event.get("audio"):
                    audio_bytes = base64.b64decode(event["audio"])
                    await self._audio_queue.put(audio_bytes)
                if event.get("isFinal"):
                    await self._audio_queue.put(None)  # sentinel per utterance
        except ConnectionClosed:
            logger.info("ElevenLabs WebSocket closed")
            await self._audio_queue.put(None)
        except Exception:
            logger.exception("ElevenLabs receive loop error")
            await self._audio_queue.put(None)

    async def close_stream(self) -> None:
        """Send the empty-string finalizer to ElevenLabs, triggering isFinal + server close.

        Waits for _receive_task to complete so all remaining audio is drained
        into the queue and the isFinal sentinel is placed.
        """
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps({"text": ""}))
            except ConnectionClosed:
                logger.debug("WS already closed when sending finalizer")
        if self._receive_task is not None:
            try:
                await self._receive_task
            except Exception:
                logger.debug("receive_task finished with error during close_stream")

    async def receive_audio(self):
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            yield chunk

    async def close(self) -> None:
        if self._receive_task is not None:
            self._receive_task.cancel()
            self._receive_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("ElevenLabs TTS closed")

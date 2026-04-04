from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator

import websockets
from websockets.exceptions import ConnectionClosed

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ElevenLabsStreamingTTS:
    def __init__(self) -> None:
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        settings = get_settings()
        voice_id = settings.tts_voice_id
        model_id = settings.elevenlabs_model_id
        logger.info(
            "ElevenLabs config: api_key_present=%s api_key_length=%d voice_id_present=%s voice_id_length=%d model_id=%s",
            bool(settings.tts_api_key),
            len(settings.tts_api_key or ""),
            bool(voice_id),
            len(voice_id or ""),
            model_id,
        )
        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
            f"?model_id={model_id}&output_format=ulaw_8000&auto_mode=true"
        )
        self._ws = await websockets.connect(
            url,
            extra_headers={"xi-api-key": settings.tts_api_key},
        )

        # Send initial config
        await self._ws.send(json.dumps({
            "text": " ",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "use_speaker_boost": True,
            },
            "generation_config": {
                "chunk_length_schedule": [120, 160, 250, 290],
            },
            "xi_api_key": settings.tts_api_key,
        }))

        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("ElevenLabs TTS connected")

    async def send_text_chunk(self, text: str, flush: bool = False) -> None:
        if self._ws is None:
            return
        payload: dict = {"text": text}
        if flush:
            payload["flush"] = True
            if text:
                payload["try_trigger_generation"] = True
        await self._ws.send(json.dumps(payload))

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                if event.get("audio"):
                    audio_bytes = base64.b64decode(event["audio"])
                    logger.info("ElevenLabs audio chunk received (%d bytes)", len(audio_bytes))
                    await self._audio_queue.put(audio_bytes)
                else:
                    logger.info("ElevenLabs event: %s", {k: v for k, v in event.items() if k != "alignment"})
                if event.get("isFinal"):
                    await self._audio_queue.put(None)  # sentinel
        except ConnectionClosed as exc:
            logger.info("ElevenLabs WebSocket closed: code=%s reason=%s", exc.code, exc.reason)
            await self._audio_queue.put(None)
        except Exception:
            logger.exception("ElevenLabs receive loop error")
            await self._audio_queue.put(None)

    async def receive_audio(self) -> AsyncGenerator[bytes, None]:
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            yield chunk

    async def close(self) -> None:
        if self._ws is not None:
            try:
                # Empty string signals ElevenLabs to finalize
                await self._ws.send(json.dumps({"text": ""}))
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task is not None:
            self._receive_task.cancel()
        self._ws = None
        logger.info("ElevenLabs TTS closed")

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import AsyncGenerator

import httpx
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
        url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{settings.tts_voice_id}/stream-input"
            f"?model_id={settings.elevenlabs_model_id}&output_format=ulaw_8000"
        )
        self._ws = await websockets.connect(
            url,
            additional_headers={"xi-api-key": settings.tts_api_key},
        )
        await self._ws.send(
            json.dumps(
                {
                    "text": " ",
                    "voice_settings": {
                        "stability": 0.45,
                        "similarity_boost": 0.75,
                        "use_speaker_boost": True,
                    },
                    "xi_api_key": settings.tts_api_key,
                }
            )
        )
        self._receive_task = asyncio.create_task(self._receive_loop())

    async def send_text_chunk(self, text: str, flush: bool = False) -> None:
        if self._ws is None:
            return
        payload: dict[str, object] = {"text": text}
        if flush:
            payload["flush"] = True
        await self._ws.send(json.dumps(payload))

    async def _receive_loop(self) -> None:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = json.loads(raw)
                audio = event.get("audio")
                if audio:
                    await self._audio_queue.put(base64.b64decode(audio))
                if event.get("isFinal"):
                    await self._audio_queue.put(None)
        except ConnectionClosed:
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
                await self._ws.send(json.dumps({"text": ""}))
                await self._ws.close()
            except Exception:
                pass
        if self._receive_task is not None:
            self._receive_task.cancel()
        self._ws = None


class CartesiaStreamingTTS:
    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def connect(self) -> None:
        self._buffer.clear()

    async def send_text_chunk(self, text: str, flush: bool = False) -> None:
        if text:
            self._buffer.append(text)
        if not flush:
            return
        transcript = "".join(self._buffer).strip()
        self._buffer.clear()
        if not transcript:
            await self._audio_queue.put(None)
            return
        settings = get_settings()
        voice = {"id": settings.tts_voice_id}
        if settings.cartesia_voice_mode != "id":
            voice = {"mode": settings.cartesia_voice_mode, "id": settings.tts_voice_id}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.cartesia.ai/tts/bytes",
                json={
                    "transcript": transcript,
                    "voice": voice,
                    "output_format": {
                        "container": "raw",
                        "encoding": "pcm_mulaw",
                        "sample_rate": 8000,
                    },
                },
                headers={
                    "X-API-Key": settings.tts_api_key,
                    "Cartesia-Version": "2024-11-13",
                },
            )
            response.raise_for_status()
            await self._audio_queue.put(response.content)
            await self._audio_queue.put(None)

    async def receive_audio(self) -> AsyncGenerator[bytes, None]:
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            yield chunk

    async def close(self) -> None:
        self._buffer.clear()
        await self._audio_queue.put(None)


def get_streaming_tts(provider: str | None = None):
    settings = get_settings()
    provider_name = (provider or settings.tts_provider or "elevenlabs").lower()
    if provider_name == "cartesia":
        return CartesiaStreamingTTS()
    return ElevenLabsStreamingTTS()

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from assemblyai.streaming.v3 import (
    Encoding,
    SpeechModel,
    StreamingClient,
    StreamingClientOptions,
    StreamingEvents,
    StreamingParameters,
    TurnEvent,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class AssemblyAIRealtimeSTT:
    def __init__(
        self,
        on_partial: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        on_final: Callable[[str, float, list], Coroutine[Any, Any, None]] | None = None,
        on_error: Callable[[Exception], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._client: StreamingClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream_task: asyncio.Task | None = None
        self._audio_buffer = bytearray()
        # AssemblyAI v3 requires >= 50ms per send; Twilio sends 20ms chunks (160 bytes at 8kHz mulaw)
        # Buffer 4 chunks = 80ms to stay safely above the minimum
        self._buffer_min_bytes = 160 * 4

    async def connect(self) -> None:
        settings = get_settings()
        self._loop = asyncio.get_event_loop()

        opts = StreamingClientOptions(api_key=settings.assemblyai_api_key)
        self._client = StreamingClient(opts)

        def on_turn(client: StreamingClient, event: TurnEvent) -> None:
            if not event.transcript:
                return
            if event.end_of_turn:
                if self._on_final and self._loop:
                    words = [{"text": w.text, "confidence": w.confidence} for w in event.words]
                    asyncio.run_coroutine_threadsafe(
                        self._on_final(event.transcript, 1.0, words),
                        self._loop,
                    )
            else:
                if self._on_partial and self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._on_partial(event.transcript),
                        self._loop,
                    )

        def on_error(client: StreamingClient, error: Exception) -> None:
            logger.error("AssemblyAI error: %s", error)
            if self._on_error and self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._on_error(error),
                    self._loop,
                )

        self._client.on(StreamingEvents.Turn, on_turn)
        self._client.on(StreamingEvents.Error, on_error)

        params = StreamingParameters(
            sample_rate=8000,
            encoding=Encoding.pcm_mulaw,
            speech_model=SpeechModel.universal_streaming_english,
            end_utterance_silence_threshold=settings.assemblyai_end_utterance_silence_ms,
        )
        self._client.connect(params)
        self._stream_task = asyncio.create_task(self._stream_loop())
        logger.info("AssemblyAI STT connected")

    async def _stream_loop(self) -> None:
        """Buffer Twilio 20ms chunks and forward once >= 80ms accumulated."""
        while self._client is not None:
            try:
                audio = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
                self._audio_buffer.extend(audio)
                if len(self._audio_buffer) >= self._buffer_min_bytes:
                    self._client.stream(bytes(self._audio_buffer))
                    self._audio_buffer.clear()
            except asyncio.TimeoutError:
                # Flush any remaining buffered audio so we don't starve AssemblyAI
                if self._audio_buffer and self._client:
                    self._client.stream(bytes(self._audio_buffer))
                    self._audio_buffer.clear()
                continue
            except Exception:
                logger.exception("AssemblyAI stream loop error")
                break

    async def send_audio(self, audio_bytes: bytes) -> None:
        await self._audio_queue.put(audio_bytes)

    async def close(self) -> None:
        if self._stream_task:
            self._stream_task.cancel()
        if self._client:
            self._client.disconnect(terminate=True)
        self._client = None
        logger.info("AssemblyAI STT closed")

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from app.core.config import get_settings
from app.hotel.guardrails import validate_agent_move
from app.hotel.schemas import AgentMove, HotelQuote, WorkerSessionState
from app.llm.fact_extractor import extract_facts_from_utterance
from app.llm.negotiation_brain import decide_move, generate_response_streaming
from app.voice.assemblyai_stt import AssemblyAIRealtimeSTT
from app.voice.elevenlabs_tts import ElevenLabsStreamingTTS
from app.voice.twilio_bridge import TwilioBridge

logger = logging.getLogger(__name__)

_MAX_GUARDRAIL_RETRIES = 2
_FALLBACK_STALL = "Let me check on that for you."


class VoicePipeline:
    def __init__(
        self,
        twilio_bridge: TwilioBridge,
        session_state: WorkerSessionState,
        on_quote_received: Callable[[HotelQuote], Coroutine[Any, Any, None]] | None = None,
        on_session_end: Callable[[WorkerSessionState], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._bridge = twilio_bridge
        self._state = session_state
        self._on_quote_received = on_quote_received
        self._on_session_end = on_session_end

        self._stt = AssemblyAIRealtimeSTT(
            on_partial=self._on_partial_transcript,
            on_final=self._on_final_transcript,
            on_error=self._on_stt_error,
        )
        self._tts = ElevenLabsStreamingTTS()
        self._done = asyncio.Event()
        self._processing_lock = asyncio.Lock()

    async def start(self) -> None:
        await self._stt.connect()
        await self._tts.connect()
        inbound = asyncio.create_task(self._inbound_loop())
        monitor = asyncio.create_task(self._monitor_loop())
        await self._done.wait()
        inbound.cancel()
        monitor.cancel()
        await self._stt.close()
        await self._tts.close()
        if self._on_session_end:
            await self._on_session_end(self._state)

    async def _inbound_loop(self) -> None:
        while not self._done.is_set():
            try:
                event = await self._bridge.receive_event()
                event_type = event.get("event")
                if event_type == "media":
                    audio = event.get("_audio_bytes", b"")
                    if audio:
                        await self._stt.send_audio(audio)
                elif event_type == "stop":
                    logger.info("Twilio stream stopped")
                    self._done.set()
            except Exception:
                logger.exception("Inbound loop error")
                self._done.set()

    async def _monitor_loop(self) -> None:
        settings = get_settings()
        max_duration = settings.max_call_duration_seconds
        import time
        start = time.monotonic()
        while not self._done.is_set():
            await asyncio.sleep(10)
            if time.monotonic() - start > max_duration:
                logger.warning("Session %s exceeded max duration", self._state.session_id)
                self._done.set()

    async def _on_partial_transcript(self, text: str) -> None:
        pass  # Could be used for barge-in detection (Phase 5)

    async def _on_final_transcript(self, text: str, confidence: float, words: list) -> None:
        # Only process one utterance at a time
        async with self._processing_lock:
            await self._process_utterance(text, confidence)

    async def _run_fact_extraction(self, text: str) -> HotelQuote | None:
        try:
            return await extract_facts_from_utterance(text, self._state)
        except Exception:
            logger.exception("Fact extraction failed")
            return None

    async def _process_utterance(self, text: str, confidence: float) -> None:
        from app.hotel.enums import SessionStatus

        self._state.transcript.append({"role": "hotel", "content": text})
        self._state.status = SessionStatus.ACTIVE

        # Run fact extraction and move decision in parallel.
        # decide_move reads quotes_received but a quote from *this* utterance
        # arriving 300ms later is acceptable — the move decision uses prior quotes.
        # The new quote is appended before the next turn so it informs future moves.
        quote, move = await asyncio.gather(
            self._run_fact_extraction(text),
            self._decide_with_guardrails(),
        )

        if quote is not None:
            self._state.quotes_received.append(quote)
            if self._on_quote_received:
                await self._on_quote_received(quote)

        # Stream response: GPT-4o -> ElevenLabs -> Twilio
        response_text = await self._speak(move)
        move.response_text = response_text
        self._state.moves_made.append(move)
        self._state.transcript.append({"role": "agent", "content": response_text})

        if move.should_terminate:
            self._done.set()

    async def _decide_with_guardrails(self) -> AgentMove:
        for attempt in range(_MAX_GUARDRAIL_RETRIES + 1):
            try:
                move = await decide_move(self._state)
                result = validate_agent_move(move, self._state.hotel_target, self._state)
                if result.allowed:
                    return move
                logger.warning("Guardrail blocked move (attempt %d): %s", attempt + 1, result.reason)
            except Exception:
                logger.exception("decide_move failed on attempt %d", attempt + 1)

        # Deterministic fallback
        from app.hotel.enums import MoveType
        return AgentMove(
            move_type=MoveType.PROBE,
            response_text=_FALLBACK_STALL,
            reasoning="guardrail fallback",
        )

    async def _stream_tokens_to_tts(self, move: AgentMove, tokens: list[str]) -> None:
        """Feed GPT-4o tokens to ElevenLabs as they arrive, then signal end."""
        token_stream = await generate_response_streaming(move, self._state)
        async for token in token_stream:
            tokens.append(token)
            await self._tts.send_text_chunk(token)
        # Empty string signals ElevenLabs to flush and finalize audio
        await self._tts.send_text_chunk("", flush=True)

    async def _forward_audio_to_twilio(self) -> None:
        """Forward ElevenLabs audio chunks to Twilio as they arrive."""
        async for audio_chunk in self._tts.receive_audio():
            await self._bridge.send_audio(audio_chunk)

    async def _speak(self, move: AgentMove) -> str:
        collected_tokens: list[str] = []
        try:
            # Run token streaming and audio forwarding concurrently.
            # ElevenLabs starts synthesizing as soon as the first tokens arrive —
            # it does not wait for GPT-4o to finish the full response.
            await asyncio.gather(
                self._stream_tokens_to_tts(move, collected_tokens),
                self._forward_audio_to_twilio(),
            )
        except Exception:
            logger.exception("Speak pipeline failed, using TwiML fallback")
            if not collected_tokens:
                return _FALLBACK_STALL

        return "".join(collected_tokens)

    async def _on_stt_error(self, exc: Exception) -> None:
        logger.error("STT error: %s", exc)
        # Reconnect logic handled at a higher level; just log here

    def signal_done(self) -> None:
        self._done.set()

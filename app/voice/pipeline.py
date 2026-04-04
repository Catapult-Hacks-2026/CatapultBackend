from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from typing import Any

from app.core.config import get_settings
from app.hotel.guardrails import validate_agent_move
from app.hotel.schemas import AgentMove, HotelQuote, WorkerSessionState
from app.llm.fact_extractor import extract_facts_from_utterance
from app.llm.negotiation_brain import decide_move, generate_response_streaming
from app.voice.assemblyai_stt import AssemblyAIRealtimeSTT
from app.voice.elevenlabs_tts import ElevenLabsStreamingTTS
from app.voice.interruption import InterruptionDetector
from app.voice.twilio_bridge import TwilioBridge

logger = logging.getLogger(__name__)

_MAX_GUARDRAIL_RETRIES = 2
_FALLBACK_STALL = "Let me check on that for you."

# Sentence boundary pattern — flush to TTS at these boundaries for lower latency
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')
_CLAUSE_END = re.compile(r'(?<=[,;:\u2014])\s+')


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

        self._interruption = InterruptionDetector()
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
        start = asyncio.get_event_loop().time()
        while not self._done.is_set():
            await asyncio.sleep(10)
            if asyncio.get_event_loop().time() - start > max_duration:
                logger.warning("Session %s exceeded max duration", self._state.session_id)
                self._done.set()

    async def _on_partial_transcript(self, text: str) -> None:
        # Feed partial transcripts to interruption detector while agent is speaking
        if self._interruption.on_partial(text):
            logger.info("Barge-in detected, clearing Twilio playback")
            await self._bridge.clear_playback()

    async def _on_final_transcript(self, text: str, confidence: float, words: list) -> None:
        logger.info("Final transcript received: %r", text)
        async with self._processing_lock:
            logger.info("Lock acquired, processing utterance: %r", text)
            await self._process_utterance(text, confidence)
            logger.info("Utterance processing complete")

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
        self._interruption.clear()

        # Fact extraction and move decision run in parallel
        quote, move = await asyncio.gather(
            self._run_fact_extraction(text),
            self._decide_with_guardrails(),
        )

        if quote is not None:
            self._state.quotes_received.append(quote)
            if self._on_quote_received:
                await self._on_quote_received(quote)

        response_text = await self._speak(move)
        move.response_text = response_text
        self._state.moves_made.append(move)
        self._state.transcript.append({"role": "agent", "content": response_text})

        if move.should_terminate:
            self._done.set()

    async def _decide_with_guardrails(self) -> AgentMove:
        logger.info("decide_with_guardrails: starting")
        for attempt in range(_MAX_GUARDRAIL_RETRIES + 1):
            try:
                move = await decide_move(self._state)
                logger.info("decide_move returned: %s", move.move_type)
                result = validate_agent_move(move, self._state.hotel_target, self._state)
                if result.allowed:
                    return move
                logger.warning("Guardrail blocked move (attempt %d): %s", attempt + 1, result.reason)
            except Exception:
                logger.exception("decide_move failed on attempt %d", attempt + 1)

        from app.hotel.enums import MoveType
        return AgentMove(
            move_type=MoveType.PROBE,
            response_text=_FALLBACK_STALL,
            reasoning="guardrail fallback",
        )

    async def _gpt_to_sentences(self, move: AgentMove, tokens: list[str]) -> None:
        """Buffer GPT-4o tokens into sentences, flush each sentence to ElevenLabs immediately.

        Flushing at sentence boundaries rather than token-by-token gives ElevenLabs
        enough context for natural prosody while still starting audio before the full
        response is generated.
        """
        token_stream = await generate_response_streaming(move, self._state)
        buffer = ""
        async for token in token_stream:
            tokens.append(token)
            buffer += token
            # Check for sentence boundary in the buffer
            parts = _SENTENCE_END.split(buffer, maxsplit=1)
            if len(parts) > 1:
                sentence, remainder = parts[0], parts[1]
                await self._tts.send_text_chunk(sentence + " ", flush=True)
                buffer = remainder
                continue
            # Fall back to clause boundary for early first flush
            if len(buffer) >= 40:
                clause_parts = _CLAUSE_END.split(buffer, maxsplit=1)
                if len(clause_parts) > 1:
                    clause, remainder = clause_parts[0], clause_parts[1]
                    await self._tts.send_text_chunk(clause + " ", flush=True)
                    buffer = remainder
        # Flush remaining buffer
        if buffer.strip():
            await self._tts.send_text_chunk(buffer, flush=True)
        # Send empty-string finalizer -> ElevenLabs sends remaining audio + isFinal -> None sentinel
        await self._tts.close_stream()

    async def _tts_to_twilio(self) -> None:
        """Forward audio chunks from ElevenLabs to Twilio, aborting on interruption."""
        async for audio_chunk in self._tts.receive_audio():
            if self._interruption.was_interrupted:
                logger.info("Interruption mid-playback, stopping audio forward")
                break
            await self._bridge.send_audio(audio_chunk)

    async def _speak(self, move: AgentMove) -> str:
        collected_tokens: list[str] = []
        self._interruption.set_speaking(True)
        try:
            logger.info("_speak: connecting TTS for move %s", move.move_type)
            await self._tts.connect()
            logger.info("_speak: starting gather for move %s", move.move_type)
            await asyncio.gather(
                self._gpt_to_sentences(move, collected_tokens),
                self._tts_to_twilio(),
            )
            logger.info("_speak: gather complete")
        except Exception:
            logger.exception("Speak pipeline failed, using fallback")
            if not collected_tokens:
                return _FALLBACK_STALL
        finally:
            self._interruption.set_speaking(False)
        return "".join(collected_tokens)

    async def _on_stt_error(self, exc: Exception) -> None:
        logger.error("STT error: %s", exc)

    def signal_done(self) -> None:
        self._done.set()

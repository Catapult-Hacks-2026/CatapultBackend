from __future__ import annotations

import asyncio
import logging
import random
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

_THINKING_STALLS = [
    "Let me look into that.",
    "One moment.",
    "Let me check our records.",
    "Let me review that.",
]

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|good\s+(morning|afternoon|evening)|how\s+are\s+you|what\s+can\s+i\s+help)",
    re.IGNORECASE,
)

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
        on_transcript_update: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._bridge = twilio_bridge
        self._state = session_state
        self._on_quote_received = on_quote_received
        self._on_session_end = on_session_end
        self._on_transcript_update = on_transcript_update

        self._interruption = InterruptionDetector()
        self._stt = AssemblyAIRealtimeSTT(
            on_partial=self._on_partial_transcript,
            on_final=self._on_final_transcript,
            on_error=self._on_stt_error,
        )
        self._tts = ElevenLabsStreamingTTS()
        self._done = asyncio.Event()
        self._processing_lock = asyncio.Lock()
        self._pending_hangup_mark: str | None = None

    async def start(self) -> None:
        await self._stt.connect()
        inbound = asyncio.create_task(self._inbound_loop())
        monitor = asyncio.create_task(self._monitor_loop())
        await self._done.wait()
        inbound.cancel()
        monitor.cancel()

        # If no outcome was set by a terminating move, derive it from state
        if self._state.outcome is None:
            from app.hotel.enums import NegotiationOutcome
            self._state.outcome = self._derive_outcome()

        if self._on_transcript_update:
            best_rate = min((q.nightly_rate for q in self._state.quotes_received), default=None)
            await self._on_transcript_update({
                "type": "call_ended",
                "outcome": str(self._state.outcome),
                "final_price": best_rate,
            })
        if self._on_session_end:
            await self._on_session_end(self._state)
        await self._stt.close()
        await self._tts.close()

    async def _inbound_loop(self) -> None:
        while not self._done.is_set():
            try:
                event = await self._bridge.receive_event()
                event_type = event.get("event")
                if event_type == "media":
                    audio = event.get("_audio_bytes", b"")
                    if audio:
                        await self._stt.send_audio(audio)
                elif event_type == "mark":
                    mark_name = event.get("mark", {}).get("name", "")
                    if mark_name == self._pending_hangup_mark:
                        logger.info("Hangup mark received, ending call")
                        self._done.set()
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
        hangup_mark_at: float | None = None
        while not self._done.is_set():
            await asyncio.sleep(1)
            now = asyncio.get_event_loop().time()
            if now - start > max_duration:
                logger.warning("Session %s exceeded max duration", self._state.session_id)
                self._done.set()
                break
            # Safety timeout: if we sent a hangup mark but never got the
            # callback, force-terminate after 10 seconds.
            if self._pending_hangup_mark is not None:
                if hangup_mark_at is None:
                    hangup_mark_at = now
                elif now - hangup_mark_at > 10:
                    logger.warning("Hangup mark timeout, forcing termination")
                    self._done.set()
                    break

    async def _on_partial_transcript(self, text: str) -> None:
        # Feed partial transcripts to interruption detector while agent is speaking
        if self._interruption.on_partial(text):
            logger.info("Barge-in detected, clearing Twilio playback")
            await self._bridge.clear_playback()
        if self._on_transcript_update:
            await self._on_transcript_update({"type": "transcript_partial", "text": text})

    async def _on_final_transcript(self, text: str, confidence: float, words: list) -> None:
        async with self._processing_lock:
            await self._process_utterance(text, confidence)

    async def _run_fact_extraction(self, text: str) -> HotelQuote | None:
        try:
            return await extract_facts_from_utterance(text, self._state)
        except Exception:
            logger.exception("Fact extraction failed")
            return None

    def _should_stall(self, text: str) -> bool:
        """Decide whether to play a filler phrase while the brain thinks."""
        if not self._state.moves_made:
            return False
        if len(text.split()) < 4:
            return False
        if _GREETING_RE.match(text.strip()):
            return False
        return True

    async def _speak_stall(self) -> None:
        """Speak a brief filler phrase via TTS to fill dead air during thinking."""
        phrase = random.choice(_THINKING_STALLS)
        try:
            await self._tts.connect()
            await self._tts.send_text_chunk(phrase, flush=True)
            await self._tts.close_stream()
            async for audio_chunk in self._tts.receive_audio():
                await self._bridge.send_audio(audio_chunk)
        except Exception:
            logger.debug("Stall phrase failed, continuing silently")

    async def _process_utterance(self, text: str, confidence: float) -> None:
        from app.hotel.enums import SessionStatus

        self._state.transcript.append({"role": "hotel", "content": text})
        if self._on_transcript_update:
            await self._on_transcript_update({
                "type": "transcript_final",
                "role": "hotel",
                "content": text,
                "index": len(self._state.transcript) - 1,
            })
        self._state.status = SessionStatus.ACTIVE
        self._interruption.clear()

        # Speak a stall phrase while the brain thinks (if appropriate)
        stall_task = None
        if self._should_stall(text):
            stall_task = asyncio.create_task(self._speak_stall())

        # Fact extraction and move decision run in parallel
        quote, move = await asyncio.gather(
            self._run_fact_extraction(text),
            self._decide_with_guardrails(),
        )

        # Wait for stall to finish before speaking the real response
        if stall_task is not None:
            await stall_task

        if quote is not None:
            if self._on_quote_received:
                await self._on_quote_received(quote)

        response_text = await self._speak(move)
        move.response_text = response_text
        self._state.moves_made.append(move)
        self._state.next_move = move
        self._state.transcript.append({"role": "agent", "content": response_text})
        if self._on_transcript_update:
            await self._on_transcript_update({
                "type": "transcript_final",
                "role": "agent",
                "content": response_text,
                "index": len(self._state.transcript) - 1,
            })

        # Record Galileo counter-offers as price changes
        if move.counter_rate and move.counter_rate > 0:
            galileo_agent_id = self._state.hotel_target.market_context.get("galileo_agent_id")
            if galileo_agent_id:
                try:
                    from app.galileo.database import record_price_change
                    await record_price_change(
                        agent_id=galileo_agent_id,
                        price=move.counter_rate,
                        source="galileo",
                    )
                    if self._on_transcript_update:
                        await self._on_transcript_update({
                            "type": "price_changed",
                            "galileo_agent_id": galileo_agent_id,
                            "price": move.counter_rate,
                            "source": "galileo",
                        })
                except Exception:
                    logger.warning("Failed to record galileo counter for agent %s", galileo_agent_id)

        if move.should_terminate:
            # Derive outcome from the terminating move
            from app.hotel.enums import MoveType, NegotiationOutcome
            if move.move_type == MoveType.ACCEPT:
                self._state.outcome = NegotiationOutcome.RATE_CONFIRMED
            elif move.move_type == MoveType.CLOSE:
                # Close without accept -- check if hotel asked for callback
                last_hotel = next(
                    (t["content"] for t in reversed(self._state.transcript) if t["role"] == "hotel"),
                    "",
                )
                if any(w in last_hotel.lower() for w in ("call back", "callback", "call you back", "follow up")):
                    self._state.outcome = NegotiationOutcome.CALLBACK_REQUESTED
                else:
                    self._state.outcome = NegotiationOutcome.NO_AVAILABILITY
            else:
                self._state.outcome = NegotiationOutcome.FAILED

            # Send a Twilio mark after the final audio. The inbound loop
            # will fire _done.set() when Twilio confirms the audio has
            # finished playing, so the caller hears the full message
            # before the call is terminated.
            try:
                mark_id = await self._bridge.send_mark("hangup")
                self._pending_hangup_mark = mark_id
                logger.info("Termination mark sent, waiting for playback to finish")
            except Exception:
                logger.warning("Failed to send hangup mark, terminating immediately")
                self._done.set()

    async def _decide_with_guardrails(self) -> AgentMove:
        guardrail_feedback: str | None = None
        for attempt in range(_MAX_GUARDRAIL_RETRIES + 1):
            try:
                move = await decide_move(self._state, guardrail_feedback=guardrail_feedback)
                result = validate_agent_move(move, self._state.hotel_target, self._state)
                if result.allowed:
                    return move
                guardrail_feedback = result.reason
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

    def _derive_outcome(self) -> "NegotiationOutcome":
        """Derive the negotiation outcome from the moves made during the call."""
        from app.hotel.enums import MoveType, NegotiationOutcome

        if not self._state.moves_made:
            return NegotiationOutcome.FAILED

        last_move = self._state.moves_made[-1]

        # Explicit accept move means rate was confirmed
        if last_move.move_type == MoveType.ACCEPT:
            return NegotiationOutcome.RATE_CONFIRMED

        # Any move that accepted is a confirmed rate
        for move in reversed(self._state.moves_made):
            if move.move_type == MoveType.ACCEPT:
                return NegotiationOutcome.RATE_CONFIRMED

        # Check transcript for callback indicators
        for entry in reversed(self._state.transcript):
            if entry["role"] == "hotel":
                text = entry["content"].lower()
                if any(w in text for w in ("call back", "callback", "call you back", "follow up", "ring you back")):
                    return NegotiationOutcome.CALLBACK_REQUESTED
                if any(w in text for w in ("no availability", "sold out", "fully booked", "no rooms")):
                    return NegotiationOutcome.NO_AVAILABILITY
                break  # only check the last hotel utterance

        # Close move without accept
        if last_move.move_type == MoveType.CLOSE:
            return NegotiationOutcome.FAILED

        # Fell through -- timed out or disconnected
        return NegotiationOutcome.TIMED_OUT

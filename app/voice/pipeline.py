from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from app.core.config import get_settings
from app.llm.fact_extractor import extract_facts_from_utterance
from app.models.schemas import ExtractedFacts, VendorOffer
from app.services.negotiation import process_vendor_input
from app.voice.deepgram_stt import DeepgramRealtimeSTT
from app.voice.tts import get_streaming_tts
from app.voice.twilio_bridge import TwilioBridge

logger = logging.getLogger(__name__)

_FALLBACK_STALL = "Let me review that and come back with the best position I can."


class VoicePipeline:
    def __init__(
        self,
        twilio_bridge: TwilioBridge,
        session_state: dict,
        on_quote_received: Callable[[ExtractedFacts], Coroutine[Any, Any, None]] | None = None,
        on_session_end: Callable[[dict], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._bridge = twilio_bridge
        self._state = session_state
        self._on_quote_received = on_quote_received
        self._on_session_end = on_session_end
        self._processing_lock = asyncio.Lock()
        self._done = asyncio.Event()
        self._stt = DeepgramRealtimeSTT(
            on_partial=self._on_partial_transcript,
            on_final=self._on_final_transcript,
            on_error=self._on_stt_error,
        )
        self._tts = get_streaming_tts()

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
        self._state["call_completed"] = True
        self._state["continue_call"] = False
        self._state["session_status"] = self._state.get("session_status") or "completed"
        if self._on_session_end:
            await self._on_session_end(self._state)

    async def _inbound_loop(self) -> None:
        while not self._done.is_set():
            try:
                event = await self._bridge.receive_event()
                event_type = event.get("event")
                if event_type == "start":
                    self._state["session_status"] = "active"
                elif event_type == "media":
                    audio = event.get("_audio_bytes", b"")
                    if audio:
                        await self._stt.send_audio(audio)
                elif event_type == "stop":
                    logger.info("Twilio stream stopped for %s", self._state["negotiation_id"])
                    self._done.set()
            except Exception:
                logger.exception("Inbound loop error for %s", self._state["negotiation_id"])
                self._state["session_status"] = "failed"
                self._done.set()

    async def _monitor_loop(self) -> None:
        max_duration = get_settings().max_call_duration_seconds
        start = asyncio.get_running_loop().time()
        while not self._done.is_set():
            await asyncio.sleep(10)
            if asyncio.get_running_loop().time() - start > max_duration:
                logger.warning("Call exceeded max duration for %s", self._state["negotiation_id"])
                self._state["final_outcome"] = "timed_out"
                self._done.set()

    async def _on_partial_transcript(self, text: str) -> None:
        logger.debug("Partial transcript for %s: %s", self._state["negotiation_id"], text)

    async def _on_final_transcript(self, text: str, confidence: float, words: list) -> None:
        async with self._processing_lock:
            await self._process_utterance(text, confidence)

    async def _process_utterance(self, text: str, confidence: float) -> None:
        logger.debug("Final transcript for %s: %s", self._state["negotiation_id"], text)
        self._state.setdefault("transcript", []).append({"role": "vendor", "content": text})
        self._state.setdefault("transcript_window", []).append(f"vendor: {text}")
        self._state["session_status"] = "active"

        extracted = await self._extract_facts(text)
        offer = self._offer_from_facts(extracted)
        if extracted is not None:
            self._state["extracted_facts"] = extracted.model_dump()
            if offer is not None:
                self._state["latest_offer"] = offer.model_dump()
            if self._on_quote_received:
                await self._on_quote_received(extracted)

        result = await asyncio.to_thread(
            process_vendor_input,
            self._state["negotiation_id"],
            text,
            offer,
        )
        reply = result.get("agent_message") or _FALLBACK_STALL
        self._state["last_agent_message"] = reply
        self._state["scoring_breakdown"] = result.get("scoring_breakdown")
        self._state["next_action"] = (result.get("agent_action") or {}).get("action")
        self._state["final_outcome"] = result.get("status")
        self._state["transcript"].append({"role": "agent", "content": reply})
        self._state["transcript_window"].append(f"agent: {reply}")
        self._trim_transcript_window()
        await self._speak(reply)

        if result.get("status") in {"accepted", "escalated", "rejected"}:
            self._done.set()

    async def _extract_facts(self, text: str) -> ExtractedFacts | None:
        try:
            return await extract_facts_from_utterance(text, self._state)
        except Exception:
            logger.exception("Fact extraction failed for %s", self._state["negotiation_id"])
            return None

    def _offer_from_facts(self, facts: ExtractedFacts | None) -> VendorOffer | None:
        if facts is None:
            return None
        previous = self._state.get("latest_offer")
        default_offer = VendorOffer.model_validate(previous) if previous else None
        return facts.to_vendor_offer(default_offer)

    def _trim_transcript_window(self) -> None:
        window = self._state.get("transcript_window", [])
        if len(window) > 12:
            self._state["transcript_window"] = window[-12:]

    async def _speak(self, text: str) -> None:
        try:
            await self._tts.send_text_chunk(text, flush=True)
            async for audio_chunk in self._tts.receive_audio():
                await self._bridge.send_audio(audio_chunk)
        except Exception:
            logger.exception("TTS playback failed for %s", self._state["negotiation_id"])

    async def _on_stt_error(self, exc: Exception) -> None:
        logger.error("STT error for %s: %s", self._state["negotiation_id"], exc)

    def signal_done(self) -> None:
        self._done.set()

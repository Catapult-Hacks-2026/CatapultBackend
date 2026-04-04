from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# Minimum word count in a partial transcript to count as a real interruption
_MIN_INTERRUPT_WORDS = 3


class InterruptionDetector:
    """Monitors AssemblyAI partial transcripts while the agent is speaking.

    If the hotel rep starts talking (>= _MIN_INTERRUPT_WORDS) while audio is
    playing, signal an interruption so the pipeline can:
      1. Clear Twilio playback
      2. Cancel the in-flight TTS stream
      3. Let the hotel rep finish, then process the new utterance
    """

    def __init__(self) -> None:
        self._speaking = False
        self._interrupted = asyncio.Event()

    def set_speaking(self, speaking: bool) -> None:
        self._speaking = speaking
        if not speaking:
            self._interrupted.clear()

    def on_partial(self, text: str) -> bool:
        """Call from the STT partial callback. Returns True if interruption detected."""
        if not self._speaking:
            return False
        word_count = len(text.split())
        if word_count >= _MIN_INTERRUPT_WORDS:
            logger.info("Interruption detected: %r (%d words)", text, word_count)
            self._interrupted.set()
            return True
        return False

    @property
    def was_interrupted(self) -> bool:
        return self._interrupted.is_set()

    async def wait_for_interruption(self) -> None:
        await self._interrupted.wait()

    def clear(self) -> None:
        self._interrupted.clear()
        self._speaking = False

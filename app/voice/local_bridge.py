"""Drop-in replacement for TwilioBridge that uses the local microphone and speakers.

Audio format matches Twilio: PCM µ-law, 8000 Hz, mono.
Requires: pip install sounddevice
"""

from __future__ import annotations

import asyncio
import audioop
import logging
import uuid

import sounddevice as sd

logger = logging.getLogger(__name__)

# Twilio streams 8 kHz µ-law mono — we match that exactly.
SAMPLE_RATE = 8000
CHANNELS = 1
BLOCK_SIZE = 160  # 20 ms frames at 8 kHz — matches typical telephony framing


class LocalBridge:
    """Mic/speaker bridge with the same interface as TwilioBridge."""

    def __init__(self) -> None:
        self._stream_sid: str = f"local-{uuid.uuid4().hex[:8]}"
        self._audio_in: asyncio.Queue[bytes] = asyncio.Queue()
        self._input_stream: sd.RawInputStream | None = None
        self._output_stream: sd.RawOutputStream | None = None
        self._started = False
        self._stopped = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def stream_sid(self) -> str:
        return self._stream_sid

    async def start(self) -> None:
        """Open mic and speaker streams. Call this before the pipeline starts."""
        self._loop = asyncio.get_running_loop()

        def _mic_callback(indata: bytes, frames: int, time_info: object, status: object) -> None:
            # indata is raw 16-bit PCM; convert to µ-law
            mulaw = audioop.lin2ulaw(bytes(indata), 2)
            if self._loop is not None and not self._stopped:
                self._loop.call_soon_threadsafe(self._audio_in.put_nowait, mulaw)

        self._input_stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCK_SIZE,
            callback=_mic_callback,
        )
        self._output_stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=BLOCK_SIZE,
        )
        self._input_stream.start()
        self._output_stream.start()
        self._started = True
        logger.info("Local audio bridge started (mic + speakers)")

    # ---- TwilioBridge-compatible interface ----

    async def receive_event(self) -> dict:
        if not self._started:
            await self.start()
            # Emit a synthetic 'start' event first, like Twilio does
            return {"event": "start", "start": {"streamSid": self._stream_sid}}

        if self._stopped:
            return {"event": "stop"}

        audio = await self._audio_in.get()
        return {
            "event": "media",
            "media": {},
            "_audio_bytes": audio,
        }

    async def send_audio(self, audio_bytes: bytes) -> None:
        if not self._output_stream or self._stopped:
            return
        # audio_bytes is µ-law; convert to 16-bit PCM for the speaker
        pcm = audioop.ulaw2lin(audio_bytes, 2)
        try:
            self._output_stream.write(pcm)
        except Exception:
            logger.debug("Speaker write error (likely cleared)")

    async def send_mark(self, name: str) -> str:
        # Marks are a Twilio sync mechanism; no-op locally
        return name or str(uuid.uuid4())

    async def clear_playback(self) -> None:
        # Drain any queued mic data to approximate clearing
        while not self._audio_in.empty():
            try:
                self._audio_in.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def stop(self) -> None:
        self._stopped = True
        if self._input_stream:
            self._input_stream.stop()
            self._input_stream.close()
        if self._output_stream:
            self._output_stream.stop()
            self._output_stream.close()
        logger.info("Local audio bridge stopped")

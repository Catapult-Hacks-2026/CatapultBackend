from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# OpenAI tts-1 streams 24kHz 16-bit PCM; Twilio expects 8kHz mulaw
# We accumulate enough samples to downsample cleanly (3:1 ratio)
_CHUNK_SAMPLES = 960  # 40ms at 24kHz, divisible by 3 -> 320 samples at 8kHz
_CHUNK_BYTES = _CHUNK_SAMPLES * 2  # 16-bit = 2 bytes per sample


class OpenAIStreamingTTS:
    def __init__(self) -> None:
        self._client = None
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._pcm_buffer = bytearray()

    async def connect(self) -> None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=get_settings().openai_api_key)
        logger.info("OpenAI TTS ready")

    async def send_text_chunk(self, text: str, flush: bool = False) -> None:
        if not text.strip() or not self._client:
            return
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                model="tts-1",
                voice="alloy",
                input=text,
                response_format="pcm",  # 24kHz 16-bit signed PCM
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=_CHUNK_BYTES):
                    self._pcm_buffer.extend(chunk)
                    while len(self._pcm_buffer) >= _CHUNK_BYTES:
                        frame = bytes(self._pcm_buffer[:_CHUNK_BYTES])
                        self._pcm_buffer = self._pcm_buffer[_CHUNK_BYTES:]
                        mulaw = _pcm24k_to_mulaw8k(frame)
                        await self._audio_queue.put(mulaw)
            # Flush any remaining buffer at end of chunk
            if flush and self._pcm_buffer:
                # Pad to multiple of 6 bytes (3 samples * 2 bytes) for clean downsampling
                remainder = len(self._pcm_buffer) % 6
                if remainder:
                    self._pcm_buffer.extend(b"\x00" * (6 - remainder))
                mulaw = _pcm24k_to_mulaw8k(bytes(self._pcm_buffer))
                self._pcm_buffer = bytearray()
                await self._audio_queue.put(mulaw)
        except Exception:
            logger.exception("OpenAI TTS synthesis failed")

    async def receive_audio(self):
        while True:
            chunk = await self._audio_queue.get()
            if chunk is None:
                break
            yield chunk

    async def close(self) -> None:
        await self._audio_queue.put(None)
        self._client = None
        logger.info("OpenAI TTS closed")


_MULAW_BIAS = 0x84
_MULAW_CLIP = 32635

def _linear_to_ulaw(sample: int) -> int:
    """Encode a 16-bit signed linear PCM sample to 8-bit mulaw."""
    sign = 0 if sample >= 0 else 0x80
    if sample < 0:
        sample = -sample
    if sample > _MULAW_CLIP:
        sample = _MULAW_CLIP
    sample += _MULAW_BIAS
    exp = 7
    for exp_mask in (0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100):
        if sample & exp_mask:
            break
        exp -= 1
    mantissa = (sample >> (exp + 3)) & 0x0F
    return ~(sign | (exp << 4) | mantissa) & 0xFF


def _pcm24k_to_mulaw8k(pcm_24k: bytes) -> bytes:
    """Downsample 24kHz 16-bit signed PCM to 8kHz mulaw (3:1 decimation)."""
    import array
    samples = array.array("h", pcm_24k)
    downsampled = samples[::3]
    return bytes(_linear_to_ulaw(s) for s in downsampled)

import asyncio
import base64
import json
import re
import uuid

import httpx
from fastapi import WebSocket
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, Say, Stream, VoiceResponse

from app.core.config import get_settings
from app.core.database import get_db
from app.services.negotiation import process_vendor_input

settings = get_settings()


def initiate_call(negotiation_id: str, vendor_phone_number: str) -> dict:
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    call = client.calls.create(
        to=vendor_phone_number,
        from_=settings.twilio_phone_number,
        url=f"{settings.base_url}/voice/twilio-stream/{negotiation_id}",
    )
    conn = get_db()
    conn.execute(
        """
        INSERT OR REPLACE INTO call_sessions (id, negotiation_id, twilio_call_sid, status, transcript)
        VALUES (?, ?, ?, ?, COALESCE((SELECT transcript FROM call_sessions WHERE negotiation_id = ?), ''))
        """,
        (str(uuid.uuid4()), negotiation_id, call.sid, "initiated", negotiation_id),
    )
    conn.commit()
    conn.close()
    return {"call_sid": call.sid, "status": call.status}


def build_twiml_response(negotiation_id: str) -> str:
    response = VoiceResponse()
    response.append(Say("Hello. This is the procurement desk calling to discuss your latest quote."))
    connect = Connect()
    connect.append(Stream(url=f"{settings.base_url.replace('http', 'ws')}/voice/media-stream/{negotiation_id}"))
    response.append(connect)
    return str(response)


async def text_to_speech(text: str) -> bytes:
    if not settings.tts_api_key or not text.strip():
        return b""

    if settings.tts_provider == "elevenlabs":
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{settings.tts_voice_id}/stream"
            "?output_format=ulaw_8000"
        )
        headers = {"xi-api-key": settings.tts_api_key}
        payload = {"text": text, "model_id": settings.elevenlabs_model_id}
    else:
        url = "https://api.cartesia.ai/tts/bytes"
        headers = {"X-API-Key": settings.tts_api_key, "Cartesia-Version": "2024-11-13"}
        voice = {"id": settings.tts_voice_id}
        if settings.cartesia_voice_mode != "id":
            voice = {"mode": settings.cartesia_voice_mode, "id": settings.tts_voice_id}
        payload = {
            "transcript": text,
            "voice": voice,
            "output_format": {
                "container": "raw",
                "encoding": "pcm_mulaw",
                "sample_rate": 8000,
            },
        }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.content


def mulaw_to_base64(audio_bytes: bytes) -> str:
    if not audio_bytes:
        return ""
    return base64.b64encode(audio_bytes).decode("utf-8")


def _split_sentences(text: str, max_chars: int = 160) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []

    chunks: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", normalized):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue

        current = ""
        for clause in re.split(r"(?<=,)\s+", sentence):
            clause = clause.strip()
            if not clause:
                continue
            candidate = f"{current} {clause}".strip() if current else clause
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = clause
            else:
                current = candidate
        if current:
            chunks.append(current)

    return chunks or [normalized]


async def stream_tts_chunks(text: str):
    for chunk in _split_sentences(text):
        audio_bytes = await text_to_speech(chunk)
        if audio_bytes:
            yield audio_bytes


async def _append_transcript(negotiation_id: str, line: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE call_sessions SET transcript = transcript || ?, status = 'active' WHERE negotiation_id = ?",
        (f"{line}\n", negotiation_id),
    )
    conn.commit()
    conn.close()


async def handle_media_stream(websocket: WebSocket, negotiation_id: str) -> None:
    await websocket.accept()
    buffer: list[str] = []
    last_message_at = asyncio.get_running_loop().time()
    stream_sid = ""

    try:
        while True:
            payload = await websocket.receive_text()
            event = json.loads(payload)
            event_type = event.get("event")

            if event_type == "start":
                stream_sid = event.get("start", {}).get("streamSid", stream_sid)
            elif event_type == "media":
                last_message_at = asyncio.get_running_loop().time()
                media = event.get("media", {})
                transcript_hint = media.get("track") == "inbound" and media.get("transcript")
                if transcript_hint:
                    buffer.append(media["transcript"])
            elif event_type == "mark":
                text = event.get("mark", {}).get("name")
                if text:
                    buffer.append(text)
            elif event_type == "stop":
                break

            if buffer and asyncio.get_running_loop().time() - last_message_at >= 2:
                utterance = " ".join(buffer).strip()
                buffer.clear()
                if not utterance:
                    continue
                await _append_transcript(negotiation_id, f"vendor: {utterance}")
                result = process_vendor_input(negotiation_id, utterance, None)
                reply = result["agent_message"]
                await _append_transcript(negotiation_id, f"agent: {reply}")
                async for audio_bytes in stream_tts_chunks(reply):
                    audio_payload = mulaw_to_base64(audio_bytes)
                    if not audio_payload:
                        continue
                    await websocket.send_json(
                        {
                            "event": "media",
                            "streamSid": stream_sid or event.get("streamSid"),
                            "media": {"payload": audio_payload},
                        }
                    )
    finally:
        conn = get_db()
        conn.execute(
            "UPDATE call_sessions SET status = 'completed' WHERE negotiation_id = ?",
            (negotiation_id,),
        )
        conn.commit()
        conn.close()

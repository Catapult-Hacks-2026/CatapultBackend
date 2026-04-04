from __future__ import annotations

import base64
import json
import logging
import uuid

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class TwilioBridge:
    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._stream_sid: str = ""

    @property
    def stream_sid(self) -> str:
        return self._stream_sid

    async def receive_event(self) -> dict:
        raw = await self._ws.receive_text()
        event = json.loads(raw)
        event_type = event.get("event")

        if event_type == "start":
            self._stream_sid = event.get("start", {}).get("streamSid", "")
            logger.debug("Twilio stream started: %s", self._stream_sid)

        elif event_type == "media":
            # Decode mulaw payload from base64
            payload = event.get("media", {}).get("payload", "")
            event["_audio_bytes"] = base64.b64decode(payload) if payload else b""

        return event

    async def send_audio(self, audio_bytes: bytes) -> None:
        if not self._stream_sid:
            return
        payload = base64.b64encode(audio_bytes).decode("utf-8")
        await self._ws.send_text(json.dumps({
            "event": "media",
            "streamSid": self._stream_sid,
            "media": {"payload": payload},
        }))

    async def send_mark(self, name: str) -> str:
        mark_id = name or str(uuid.uuid4())
        await self._ws.send_text(json.dumps({
            "event": "mark",
            "streamSid": self._stream_sid,
            "mark": {"name": mark_id},
        }))
        return mark_id

    async def clear_playback(self) -> None:
        if not self._stream_sid:
            return
        await self._ws.send_text(json.dumps({
            "event": "clear",
            "streamSid": self._stream_sid,
        }))

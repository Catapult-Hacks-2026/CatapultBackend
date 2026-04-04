import asyncio
import base64
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import WebSocket
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, Say, Stream, VoiceResponse

from app.core.cache import cache_delete, cache_set, cache_set_if_absent
from app.core.config import get_settings
from app.core.database import get_db
from app.models.enums import NegotiationStatus, WorkerEventType
from app.models.schemas import WorkerEvent
from app.services.events import publish_worker_event
from app.services.llm import decide_move_fast, extract_facts_fast, generate_reply_fast
from app.services.memory import (
    extract_memory_candidates,
    load_negotiation_working_memory_from_redis,
    load_vendor_priors,
    refresh_category_working_memory,
    refresh_negotiation_working_memory,
    store_memory_candidates,
    store_negotiation_working_memory_in_redis,
)
from app.services.negotiation import process_vendor_input

settings = get_settings()


@dataclass
class WorkerSession:
    thread_id: str
    negotiation_id: str
    vendor_name: str
    product_category: str
    campaign_id: Optional[str]
    buyer_config: dict
    session_status: str = "setup"
    transcript_window: list[str] = field(default_factory=list)
    latest_quote: Optional[dict] = None
    fees_restrictions: Optional[dict] = None
    objections_used: list[str] = field(default_factory=list)
    negotiation_strategy: Optional[dict] = None
    manager_reached: bool = False
    callback_requested: bool = False
    continue_call: bool = True
    next_action: Optional[str] = None
    final_outcome: Optional[str] = None


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


async def load_vendor_context(negotiation_id: str) -> dict:
    cached = await load_negotiation_working_memory_from_redis(negotiation_id)
    if isinstance(cached, dict):
        conn = get_db()
        negotiation = conn.execute(
            "SELECT * FROM negotiations WHERE id = ?",
            (negotiation_id,),
        ).fetchone()
        conn.close()
        if negotiation is not None:
            return {
                "negotiation": negotiation,
                "messages": cached.get("messages", []),
                "latest_quote": cached.get("latest_quote"),
            }

    conn = get_db()
    negotiation = conn.execute(
        "SELECT * FROM negotiations WHERE id = ?",
        (negotiation_id,),
    ).fetchone()
    if negotiation is None:
        conn.close()
        raise ValueError(f"Negotiation {negotiation_id} not found")
    messages = conn.execute(
        "SELECT role, content, extracted_facts, created_at FROM messages WHERE negotiation_id = ? ORDER BY created_at, id",
        (negotiation_id,),
    ).fetchall()
    latest_quotes = conn.execute(
        """
        SELECT vendor_name, product_category, unit_price, shipping_cost, payment_terms_days, delivery_days,
               confidence_score, restrictions, quote_timestamp
        FROM latest_quotes
        WHERE negotiation_id = ?
        ORDER BY quote_timestamp DESC
        LIMIT 1
        """,
        (negotiation_id,),
    ).fetchone()
    conn.close()
    payload = {
        "negotiation": negotiation,
        "messages": [dict(row) for row in messages],
        "latest_quote": dict(latest_quotes) if latest_quotes is not None else None,
    }
    await store_negotiation_working_memory_in_redis(
        negotiation_id,
        {
            "negotiation_id": negotiation_id,
            "vendor_name": negotiation["vendor_name"],
            "product_category": negotiation["product_category"],
            "buyer_config": json.loads(negotiation["config"]),
            "messages": payload["messages"],
            "latest_quote": payload["latest_quote"],
            "compiled_at": datetime.now(timezone.utc).isoformat(),
            "source": "sqlite_negotiation_context",
        },
        ttl=settings.working_memory_ttl,
    )
    return payload


async def setup_worker_session(negotiation_id: str) -> WorkerSession:
    context = await load_vendor_context(negotiation_id)
    negotiation = context["negotiation"]
    priors = await load_vendor_priors(negotiation["vendor_name"], negotiation["product_category"])
    session = WorkerSession(
        thread_id=negotiation["thread_id"] or str(uuid.uuid4()),
        negotiation_id=negotiation_id,
        vendor_name=negotiation["vendor_name"],
        product_category=negotiation["product_category"],
        campaign_id=negotiation["campaign_id"],
        buyer_config=json.loads(negotiation["config"]),
        latest_quote=context["latest_quote"],
        negotiation_strategy=priors,
    )
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE negotiations
        SET thread_id = ?, worker_status = ?, call_started_at = COALESCE(call_started_at, ?), updated_at = ?
        WHERE id = ?
        """,
        (session.thread_id, "calling", now, now, negotiation_id),
    )
    conn.commit()
    conn.close()
    acquired = await acquire_session_lock(
        session.vendor_name,
        session.product_category,
        session.negotiation_id,
        session.thread_id,
    )
    if not acquired:
        session.continue_call = False
        session.final_outcome = "lock_conflict"
    if session.campaign_id:
        await publish_worker_event(
            session.campaign_id,
            WorkerEvent(
                event_type=WorkerEventType.TERMINATED if not acquired else WorkerEventType.CALL_STARTED,
                negotiation_id=negotiation_id,
                campaign_id=session.campaign_id,
                data={"vendor_name": session.vendor_name, "reason": "lock_conflict" if not acquired else None},
                timestamp=datetime.now(timezone.utc),
            ),
        )
    return session


async def acquire_session_lock(
    vendor_name: str,
    product_category: str,
    negotiation_id: str,
    worker_id: str,
) -> bool:
    lock_key = f"lock:{vendor_name}:{product_category}"
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=settings.session_lock_ttl)).isoformat()
    acquired = await cache_set_if_absent(
        lock_key,
        {"negotiation_id": negotiation_id, "worker_id": worker_id},
        ttl=settings.session_lock_ttl,
    )
    conn = get_db()
    if acquired:
        conn.execute(
            """
            INSERT OR REPLACE INTO session_locks (lock_key, negotiation_id, expires_at, worker_id)
            VALUES (?, ?, ?, ?)
            """,
            (lock_key, negotiation_id, expires_at, worker_id),
        )
        conn.commit()
    conn.close()
    return acquired


async def release_session_lock(session: WorkerSession) -> None:
    lock_key = f"lock:{session.vendor_name}:{session.product_category}"
    await cache_delete(lock_key)
    conn = get_db()
    conn.execute("DELETE FROM session_locks WHERE lock_key = ?", (lock_key,))
    conn.execute(
        "UPDATE negotiations SET worker_status = ?, call_ended_at = ?, updated_at = ? WHERE id = ?",
        ("completed", datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), session.negotiation_id),
    )
    conn.commit()
    conn.close()


async def listen_for_turn(
    websocket: WebSocket,
    session: WorkerSession,
    buffer: list[str],
    event: dict,
) -> Optional[str]:
    event_type = event.get("event")
    if event_type == "media":
        media = event.get("media", {})
        transcript_hint = media.get("track") == "inbound" and media.get("transcript")
        if transcript_hint:
            buffer.append(media["transcript"])
    elif event_type == "mark":
        text = event.get("mark", {}).get("name")
        if text:
            buffer.append(text)
    elif event_type == "stop":
        session.continue_call = False
    if not buffer:
        return None
    utterance = " ".join(buffer).strip()
    buffer.clear()
    return utterance or None


async def extract_structured_facts(utterance: str, session: WorkerSession) -> dict:
    return await extract_facts_fast([utterance], {"vendor_name": session.vendor_name})


async def persist_extracted_facts(negotiation_id: str, utterance: str, facts: dict) -> None:
    conn = get_db()
    row = conn.execute(
        """
        SELECT id
        FROM messages
        WHERE negotiation_id = ? AND role = 'vendor' AND content = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (negotiation_id, utterance),
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE messages SET extracted_facts = ? WHERE id = ?",
            (json.dumps(facts), row["id"]),
        )
        conn.commit()
    conn.close()
    await refresh_negotiation_working_memory(negotiation_id, ttl=settings.working_memory_ttl)


async def write_quote_update(session: WorkerSession, facts: dict) -> None:
    if facts.get("quoted_rate") is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    restrictions = facts.get("fees") or {}
    conn = get_db()
    conn.execute(
        """
        INSERT INTO quote_events (
            id, negotiation_id, vendor_name, product_category, unit_price, shipping_cost,
            payment_terms_days, delivery_days, confidence_score, restrictions, source, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            session.negotiation_id,
            session.vendor_name,
            session.product_category,
            facts["quoted_rate"],
            facts.get("shipping_cost"),
            facts.get("payment_terms_days"),
            facts.get("delivery_days"),
            facts.get("negotiation_openness", 1.0),
            json.dumps(restrictions),
            "call",
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO latest_quotes (
            vendor_name, product_category, unit_price, shipping_cost, payment_terms_days,
            delivery_days, confidence_score, restrictions, quote_timestamp, negotiation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(vendor_name, product_category)
        DO UPDATE SET
            unit_price = excluded.unit_price,
            shipping_cost = excluded.shipping_cost,
            payment_terms_days = excluded.payment_terms_days,
            delivery_days = excluded.delivery_days,
            confidence_score = excluded.confidence_score,
            restrictions = excluded.restrictions,
            quote_timestamp = excluded.quote_timestamp,
            negotiation_id = excluded.negotiation_id
        """,
        (
            session.vendor_name,
            session.product_category,
            facts["quoted_rate"],
            facts.get("shipping_cost"),
            facts.get("payment_terms_days"),
            facts.get("delivery_days"),
            facts.get("negotiation_openness", 1.0),
            json.dumps(restrictions),
            now,
            session.negotiation_id,
        ),
    )
    conn.execute(
        """
        UPDATE negotiations
        SET current_offer = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(
                {
                    "unit_price": facts["quoted_rate"],
                    "shipping_cost": facts.get("shipping_cost") or 0.0,
                    "payment_terms_days": facts.get("payment_terms_days") or 30,
                    "delivery_days": facts.get("delivery_days") or 14,
                    "notes": facts.get("product_details"),
                }
            ),
            now,
            session.negotiation_id,
        ),
    )
    conn.commit()
    conn.close()
    session.latest_quote = facts
    await cache_set(
        f"quote:{session.vendor_name}:{session.product_category}",
        facts,
        ttl=settings.quote_cache_ttl,
    )
    await refresh_negotiation_working_memory(
        session.negotiation_id,
        ttl=settings.working_memory_ttl,
    )
    await refresh_category_working_memory(
        session.product_category,
        ttl=settings.working_memory_ttl,
    )
    if session.campaign_id:
        await publish_worker_event(
            session.campaign_id,
            WorkerEvent(
                event_type=WorkerEventType.QUOTE_RECEIVED,
                negotiation_id=session.negotiation_id,
                campaign_id=session.campaign_id,
                data=facts,
                timestamp=datetime.now(timezone.utc),
            ),
        )


async def check_cross_session_state(session: WorkerSession) -> bool:
    conn = get_db()
    better_quote = conn.execute(
        """
        SELECT unit_price
        FROM latest_quotes
        WHERE product_category = ?
          AND negotiation_id != ?
        ORDER BY unit_price ASC
        LIMIT 1
        """,
        (session.product_category, session.negotiation_id),
    ).fetchone()
    conn.close()
    if better_quote and session.latest_quote and better_quote["unit_price"] < session.latest_quote.get("quoted_rate", float("inf")):
        session.final_outcome = "terminated_better_quote_exists"
        return False
    return True


async def decide_negotiation_move(session: WorkerSession, facts: dict) -> str:
    result = await decide_move_fast(
        {
            "vendor_name": session.vendor_name,
            "buyer_config": session.buyer_config,
        },
        facts,
        session.negotiation_strategy or {},
    )
    session.next_action = result["action"]
    return result["action"]


async def generate_response_text(session: WorkerSession, action: str) -> str:
    return await generate_reply_fast(
        action,
        {
            "vendor_name": session.vendor_name,
            "buyer_config": session.buyer_config,
        },
    )


async def speak_response(websocket: WebSocket, stream_sid: str, response_text: str) -> None:
    async for audio_bytes in stream_tts_chunks(response_text):
        audio_payload = mulaw_to_base64(audio_bytes)
        if not audio_payload:
            continue
        await websocket.send_json(
            {
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": audio_payload},
            }
        )


def evaluate_termination(session: WorkerSession) -> bool:
    if session.next_action == "accept":
        session.final_outcome = "accepted"
        return False
    if session.callback_requested:
        session.final_outcome = "callback_requested"
        return False
    return True


async def post_call_summary(session: WorkerSession) -> dict:
    conn = get_db()
    call_row = conn.execute(
        """
        SELECT transcript FROM call_sessions
        WHERE negotiation_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session.negotiation_id,),
    ).fetchone()
    transcript = (call_row["transcript"] if call_row else "").strip()
    outcome = {
        "final_outcome": session.final_outcome or "completed",
        "quoted_rate": (session.latest_quote or {}).get("quoted_rate"),
        "manager_reached": session.manager_reached,
        "callback_requested": session.callback_requested,
        "transcript_excerpt": transcript[-1000:],
    }
    conn.execute(
        """
        UPDATE negotiations
        SET final_outcome = ?, manager_reached = ?, callback_requested = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            outcome["final_outcome"],
            int(session.manager_reached),
            int(session.callback_requested),
            NegotiationStatus.ACCEPTED.value if outcome["final_outcome"] == "accepted" else NegotiationStatus.AWAITING_VENDOR.value,
            datetime.now(timezone.utc).isoformat(),
            session.negotiation_id,
        ),
    )
    conn.commit()
    conn.close()
    await refresh_negotiation_working_memory(
        session.negotiation_id,
        ttl=settings.working_memory_ttl,
    )
    return outcome


async def emit_memory_candidates(session: WorkerSession) -> None:
    conn = get_db()
    transcript_rows = conn.execute(
        """
        SELECT role, content
        FROM messages
        WHERE negotiation_id = ?
        ORDER BY created_at, id
        """,
        (session.negotiation_id,),
    ).fetchall()
    conn.close()
    transcript = [f"{row['role']}: {row['content']}" for row in transcript_rows]
    candidates = await extract_memory_candidates(
        session.negotiation_id,
        transcript,
        session.latest_quote or {},
    )
    await store_memory_candidates(candidates)


async def handle_media_stream(websocket: WebSocket, negotiation_id: str) -> None:
    await websocket.accept()
    session = await setup_worker_session(negotiation_id)
    if not session.continue_call:
        await release_session_lock(session)
        return
    buffer: list[str] = []
    stream_sid = ""

    try:
        while session.continue_call:
            payload = await websocket.receive_text()
            event = json.loads(payload)
            event_type = event.get("event")

            if event_type == "start":
                stream_sid = event.get("start", {}).get("streamSid", stream_sid)
            utterance = await listen_for_turn(websocket, session, buffer, event)
            if not utterance:
                continue
            session.session_status = "active"
            session.transcript_window.append(utterance)
            await _append_transcript(negotiation_id, f"vendor: {utterance}")
            facts = await extract_structured_facts(utterance, session)
            if "manager" in utterance.lower():
                session.manager_reached = True
            if "call back" in utterance.lower() or "callback" in utterance.lower():
                session.callback_requested = True
            await write_quote_update(session, facts)
            should_continue = await check_cross_session_state(session)
            if not should_continue:
                break
            action = await decide_negotiation_move(session, facts)
            reply = await generate_response_text(session, action)
            result = process_vendor_input(negotiation_id, utterance, None)
            await persist_extracted_facts(negotiation_id, utterance, facts)
            reply = result["agent_message"] if result.get("agent_message") else reply
            await _append_transcript(negotiation_id, f"agent: {reply}")
            await speak_response(websocket, stream_sid or event.get("streamSid", ""), reply)
            session.continue_call = evaluate_termination(session)
    finally:
        session.session_status = "post_call"
        outcome = await post_call_summary(session)
        await emit_memory_candidates(session)
        await release_session_lock(session)
        conn = get_db()
        conn.execute(
            "UPDATE call_sessions SET status = 'completed' WHERE negotiation_id = ?",
            (negotiation_id,),
        )
        conn.commit()
        conn.close()
        if session.campaign_id:
            from app.services.campaign import handle_job_completion

            await handle_job_completion(
                session.campaign_id,
                session.negotiation_id,
                success=bool(session.latest_quote),
                outcome=outcome,
            )
            await publish_worker_event(
                session.campaign_id,
                WorkerEvent(
                    event_type=WorkerEventType.DEAL_CLOSED if session.latest_quote else WorkerEventType.TERMINATED,
                    negotiation_id=session.negotiation_id,
                    campaign_id=session.campaign_id,
                    data=outcome,
                    timestamp=datetime.now(timezone.utc),
                ),
            )

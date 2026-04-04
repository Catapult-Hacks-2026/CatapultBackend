from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.hotel.enums import NegotiationOutcome, SessionStatus
from app.hotel.schemas import WorkerSessionState
from app.orchestration.session_lock import get_lock_manager

logger = logging.getLogger(__name__)

_MAX_TURNS = 20


def _backend_url(path: str) -> str:
    return f"{get_settings().base_url}{path}"


async def load_context_node(state: WorkerSessionState) -> dict:
    hotel_id = state.hotel_target.hotel_id
    async with httpx.AsyncClient() as client:
        try:
            hotel_resp = await client.get(_backend_url(f"/api/hotels/{hotel_id}"))
            hotel_data = hotel_resp.json() if hotel_resp.status_code == 200 else {}
        except Exception as exc:
            logger.warning("load_context: failed to fetch hotel %s: %s", hotel_id, exc)
            hotel_data = {}

        try:
            quotes_resp = await client.get(_backend_url(f"/api/hotels/{hotel_id}/quotes"))
            prior_quotes = quotes_resp.json() if quotes_resp.status_code == 200 else []
        except Exception as exc:
            logger.warning("load_context: failed to fetch quotes for %s: %s", hotel_id, exc)
            prior_quotes = []

    # Distill only negotiation-relevant fields — not the full API response
    prior_low = min((q.get("nightly_rate", 0) for q in prior_quotes if q.get("nightly_rate")), default=None)
    prior_count = len(prior_quotes)

    negotiation_summary = None
    if prior_count > 0 and prior_low is not None:
        negotiation_summary = (
            f"{prior_count} prior quote(s) on record. "
            f"Lowest historical rate: ${prior_low:.2f}/night."
        )

    return {
        "behavioral_priors": {
            "negotiation_summary": negotiation_summary,
        }
    }


async def load_memory_node(state: WorkerSessionState) -> dict:
    # Phase 2 will populate this from BehavioralStore; stub for Phase 1
    return {}


async def acquire_lock_node(state: WorkerSessionState) -> dict:
    manager = get_lock_manager()
    acquired = await manager.acquire(state.hotel_target.hotel_id, state.session_id)
    return {"lock_acquired": acquired}


def route_lock(state: WorkerSessionState) -> str:
    return "acquired" if state.lock_acquired else "locked"


async def start_voice_node(state: WorkerSessionState) -> dict:
    settings = get_settings()
    try:
        from twilio.rest import Client as TwilioClient
        client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        twiml_url = f"{settings.base_url}/voice/twilio-stream/{state.session_id}"
        call = client.calls.create(
            to=state.hotel_target.phone_number,
            from_=settings.twilio_phone_number,
            url=twiml_url,
        )
        return {"call_sid": call.sid, "status": SessionStatus.RINGING}
    except Exception as exc:
        logger.error("start_voice_node: Twilio call failed: %s", exc)
        return {"status": SessionStatus.FAILED, "error_log": state.error_log + [str(exc)]}


async def listen_node(state: WorkerSessionState) -> dict:
    # In practice the VoicePipeline drives the listen/respond cycle;
    # this node is a logical marker. The pipeline runs via handle_media_stream_connected().
    return {}


async def extract_facts_node(state: WorkerSessionState) -> dict:
    # Called from within VoicePipeline._process_utterance directly.
    return {}


async def sync_quote_node(state: WorkerSessionState) -> dict:
    if not state.quotes_received:
        return {}
    latest = state.quotes_received[-1]
    async with httpx.AsyncClient() as client:
        try:
            await client.post(_backend_url("/api/quotes/"), json={
                "session_id": state.session_id,
                "hotel_id": state.hotel_target.hotel_id,
                "nightly_rate": latest.nightly_rate,
                "total_rate": latest.total_rate,
                "inclusions": latest.inclusions,
                "cancellation_policy": latest.cancellation_policy,
                "rate_type": latest.rate_type,
                "fees": latest.fees,
                "confidence": latest.confidence,
            })
        except Exception as exc:
            logger.warning("sync_quote_node: POST /api/quotes/ failed: %s", exc)
    return {}


async def check_cross_session_node(state: WorkerSessionState) -> dict:
    # Phase 4 stub: will read campaign market state
    return {}


async def decide_move_node(state: WorkerSessionState) -> dict:
    # Decision happens inside VoicePipeline; node is a logical marker
    return {}


async def speak_node(state: WorkerSessionState) -> dict:
    # Execution happens inside VoicePipeline; node is a logical marker
    return {}


async def check_terminate_node(state: WorkerSessionState) -> dict:
    return {}


def route_terminate(state: WorkerSessionState) -> str:
    if state.next_move and state.next_move.should_terminate:
        return "done"
    if len(state.moves_made) >= _MAX_TURNS:
        return "done"
    if state.status in (SessionStatus.FAILED, SessionStatus.COMPLETED):
        return "done"
    return "continue"


async def post_call_node(state: WorkerSessionState) -> dict:
    from app.llm.openai_client import invoke_json
    from app.llm.prompts import POST_CALL_ANALYSIS_SYSTEM

    transcript_text = "\n".join(f"{t['role']}: {t['content']}" for t in state.transcript)
    try:
        data = await invoke_json(POST_CALL_ANALYSIS_SYSTEM, transcript_text, temperature=0.0)
        outcome_str = data.get("outcome", "failed")
        try:
            outcome = NegotiationOutcome(outcome_str)
        except ValueError:
            outcome = NegotiationOutcome.FAILED

        async with httpx.AsyncClient() as client:
            await client.patch(_backend_url(f"/api/sessions/{state.session_id}"), json={
                "status": SessionStatus.COMPLETED,
                "outcome": outcome,
                "transcript": state.transcript,
                "summary": data.get("summary", ""),
                "key_patterns": data.get("key_patterns", []),
                "lessons": data.get("lessons", []),
            })

        return {"status": SessionStatus.COMPLETED, "outcome": outcome}
    except Exception as exc:
        logger.exception("post_call_node failed: %s", exc)
        return {"status": SessionStatus.COMPLETED, "outcome": NegotiationOutcome.FAILED}


async def emit_memory_node(state: WorkerSessionState) -> dict:
    # Phase 2: emit behavioral features to BehavioralStore
    return {}


async def release_lock_node(state: WorkerSessionState) -> dict:
    manager = get_lock_manager()
    manager.release(state.hotel_target.hotel_id, state.session_id)
    return {"lock_acquired": False}

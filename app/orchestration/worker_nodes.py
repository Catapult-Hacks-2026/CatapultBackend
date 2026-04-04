from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.core.events import EventType, WorkerEvent, get_event_bus
from app.core.urls import build_public_url, build_upstream_url
from app.hotel.enums import NegotiationOutcome, SessionStatus
from app.hotel.schemas import WorkerSessionState
from app.orchestration.session_lock import get_lock_manager

logger = logging.getLogger(__name__)

_MAX_TURNS = 20
# Threshold: if another session has a confirmed rate within this % of our target, skip the call
_CROSS_SESSION_SKIP_THRESHOLD = 0.05


def _backend_url(path: str) -> str:
    return build_upstream_url(path)


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
    from app.memory.behavioral_store import get_behavioral_store
    store = get_behavioral_store()
    profile = await store.load_priors(state.hotel_target.hotel_id)
    return {
        "behavioral_priors": {
            "negotiation_summary": profile.to_prompt_context(),
        }
    }


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
        twiml_url = build_public_url(f"/voice/twilio-stream/{state.session_id}")
        call = client.calls.create(
            to=state.hotel_target.phone_number,
            from_=settings.twilio_phone_number,
            url=twiml_url,
        )
        # Emit WORKER_STARTED event
        await get_event_bus().publish(WorkerEvent(
            event_type=EventType.WORKER_STARTED,
            session_id=state.session_id,
            campaign_id=state.campaign_id,
            hotel_id=state.hotel_target.hotel_id,
            payload={"call_sid": call.sid},
        ))
        return {"call_sid": call.sid, "status": SessionStatus.RINGING}
    except Exception as exc:
        logger.error("start_voice_node: Twilio call failed: %s", exc)
        await get_event_bus().publish(WorkerEvent(
            event_type=EventType.WORKER_FAILED,
            session_id=state.session_id,
            campaign_id=state.campaign_id,
            hotel_id=state.hotel_target.hotel_id,
            payload={"reason": str(exc)},
        ))
        return {"status": SessionStatus.FAILED, "error_log": state.error_log + [str(exc)]}


async def listen_node(state: WorkerSessionState) -> dict:
    # VoicePipeline drives the listen/respond cycle via handle_media_stream_connected()
    return {}


async def extract_facts_node(state: WorkerSessionState) -> dict:
    # Called from within VoicePipeline._process_utterance directly
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
            })
        except Exception as exc:
            logger.warning("sync_quote_node: POST /api/quotes/ failed: %s", exc)

    # Emit QUOTE_RECEIVED so campaign controller and other subscribers are aware
    await get_event_bus().publish(WorkerEvent(
        event_type=EventType.QUOTE_RECEIVED,
        session_id=state.session_id,
        campaign_id=state.campaign_id,
        hotel_id=state.hotel_target.hotel_id,
        payload={
            "nightly_rate": latest.nightly_rate,
            "rate_type": latest.rate_type,
        },
    ))
    return {}


async def check_cross_session_node(state: WorkerSessionState) -> dict:
    """Check if another active session has already secured a good rate for this hotel."""
    target = state.hotel_target
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(_backend_url(f"/api/market/state"))
            if resp.status_code != 200:
                return {}
            market = resp.json()

        confirmed_quotes = market.get("confirmed_quotes", {})
        hotel_confirmed = confirmed_quotes.get(target.hotel_id)

        if hotel_confirmed is not None:
            confirmed_rate = float(hotel_confirmed.get("nightly_rate", 0))
            # If another session confirmed a rate within 5% of our target, no need to continue
            if confirmed_rate > 0 and confirmed_rate <= target.target_rate * (1 + _CROSS_SESSION_SKIP_THRESHOLD):
                logger.info(
                    "cross_session: hotel %s already has confirmed rate $%.2f, terminating session %s",
                    target.hotel_id, confirmed_rate, state.session_id,
                )
                await get_event_bus().publish(WorkerEvent(
                    event_type=EventType.DUPLICATE_DETECTED,
                    session_id=state.session_id,
                    campaign_id=state.campaign_id,
                    hotel_id=target.hotel_id,
                    payload={"confirmed_rate": confirmed_rate, "reason": "cross_session_duplicate"},
                ))
                return {"next_move": state.next_move}  # termination handled by pipeline

    except Exception as exc:
        logger.warning("check_cross_session_node failed: %s", exc)

    return {}


async def decide_move_node(state: WorkerSessionState) -> dict:
    # Decision happens inside VoicePipeline._process_utterance
    return {}


async def speak_node(state: WorkerSessionState) -> dict:
    # Execution happens inside VoicePipeline._process_utterance
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
    from app.llm.post_call_analyzer import analyze_call
    from app.memory.behavioral_store import get_behavioral_store

    try:
        analysis = await analyze_call(state)

        async with httpx.AsyncClient() as client:
            await client.patch(_backend_url(f"/api/sessions/{state.session_id}"), json={
                "status": SessionStatus.COMPLETED,
                "outcome": analysis.outcome,
                "transcript": state.transcript,
                "summary": analysis.summary,
                "key_patterns": analysis.key_patterns,
                "lessons": analysis.lessons,
                "call_quality_score": analysis.call_quality_score,
                "follow_up_recommended": analysis.follow_up_recommended,
                "follow_up_reason": analysis.follow_up_reason,
            })

        store = get_behavioral_store()
        await store.store_call_summary(
            session_id=state.session_id,
            hotel_id=state.hotel_target.hotel_id,
            summary=analysis.summary,
            outcome=analysis.outcome,
            quotes=state.quotes_received,
        )

        best_rate = min((q.nightly_rate for q in state.quotes_received), default=None)

        # Emit outcome event
        event_type = (
            EventType.DEAL_CLOSED
            if analysis.outcome == NegotiationOutcome.RATE_CONFIRMED
            else EventType.CALLBACK_REQUESTED
            if analysis.outcome == NegotiationOutcome.CALLBACK_REQUESTED
            else EventType.WORKER_COMPLETED
        )
        await get_event_bus().publish(WorkerEvent(
            event_type=event_type,
            session_id=state.session_id,
            campaign_id=state.campaign_id,
            hotel_id=state.hotel_target.hotel_id,
            payload={"outcome": analysis.outcome, "best_rate": best_rate},
        ))

        return {"status": SessionStatus.COMPLETED, "outcome": analysis.outcome}
    except Exception as exc:
        logger.exception("post_call_node failed: %s", exc)
        await get_event_bus().publish(WorkerEvent(
            event_type=EventType.WORKER_FAILED,
            session_id=state.session_id,
            campaign_id=state.campaign_id,
            hotel_id=state.hotel_target.hotel_id,
            payload={"reason": str(exc)},
        ))
        return {"status": SessionStatus.COMPLETED, "outcome": NegotiationOutcome.FAILED}


async def emit_memory_node(state: WorkerSessionState) -> dict:
    from app.memory.behavioral_store import get_behavioral_store
    from app.memory.memory_candidates import extract_memory_candidates

    try:
        features = await extract_memory_candidates(state)
        if features:
            store = get_behavioral_store()
            await store.store_features(features)
            logger.info(
                "emit_memory_node: stored %d features for hotel %s",
                len(features),
                state.hotel_target.hotel_id,
            )
    except Exception:
        logger.exception("emit_memory_node failed for session %s", state.session_id)

    return {}


async def release_lock_node(state: WorkerSessionState) -> dict:
    manager = get_lock_manager()
    manager.release(state.hotel_target.hotel_id, state.session_id)
    return {"lock_acquired": False}

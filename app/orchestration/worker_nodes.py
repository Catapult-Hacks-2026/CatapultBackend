from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.shared_clients import get_http_client
from app.core.events import EventType, WorkerEvent, get_event_bus
from app.hotel.enums import NegotiationOutcome, SessionStatus
from app.hotel.schemas import WorkerSessionState
from app.orchestration.session_lock import get_lock_manager

logger = logging.getLogger(__name__)

_MAX_TURNS = 20
# Threshold: if another session has a confirmed rate within this % of our target, skip the call
_CROSS_SESSION_SKIP_THRESHOLD = 0.05


def _backend_url(path: str) -> str:
    return f"{get_settings().base_url}{path}"


async def load_context_node(state: WorkerSessionState) -> dict:
    hotel_id = state.hotel_target.hotel_id
    client = get_http_client()
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

    prior_low = min((q.get("nightly_rate", 0) for q in prior_quotes if q.get("nightly_rate")), default=None)
    prior_count = len(prior_quotes)

    negotiation_summary = None
    if prior_count > 0 and prior_low is not None:
        negotiation_summary = (
            f"{prior_count} prior quote(s) on record. "
            f"Lowest historical rate: ${prior_low:.2f}/night."
        )

    # Fetch market intelligence from Redis (historic rates + past deals)
    from app.services.market_data import get_market_context
    market_brief = ""
    try:
        location = state.hotel_target.market_context.get("location", "")
        hotel_name = state.hotel_target.market_context.get("hotel_name", hotel_id)
        check_in_month = None
        if state.hotel_target.check_in:
            try:
                check_in_month = int(state.hotel_target.check_in.split("-")[1])
            except (IndexError, ValueError):
                pass
        market_brief = get_market_context(hotel_name, location, check_in_month)
    except Exception as exc:
        logger.warning("load_context: market data retrieval failed: %s", exc)

    return {
        "behavioral_priors": {
            "negotiation_summary": negotiation_summary,
            "market_brief": market_brief,
        }
    }


async def load_memory_node(state: WorkerSessionState) -> dict:
    from app.memory.behavioral_store import get_behavioral_store
    store = get_behavioral_store()
    profile = await store.load_priors(state.hotel_target.hotel_id)
    # Merge with existing priors (preserves market_brief from load_context_node)
    merged = dict(state.behavioral_priors)
    merged["negotiation_summary"] = profile.to_prompt_context()
    return {
        "behavioral_priors": merged,
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
        destination_number = state.hotel_target.phone_number or settings.twilio_to_phone_number
        if not destination_number:
            raise ValueError("No outbound destination configured. Set hotel_target.phone_number or TWILIO_TO_PHONE_NUMBER.")
        client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
        twiml_url = f"{settings.base_url}/voice/twilio-stream/{state.session_id}"
        status_callback_url = f"{settings.base_url}/voice/status/{state.session_id}"
        logger.info(
            "start_voice_node: creating Twilio call session_id=%s campaign_id=%s hotel_id=%s to=%s from=%s twiml_url=%s status_callback_url=%s",
            state.session_id,
            state.campaign_id,
            state.hotel_target.hotel_id,
            destination_number,
            settings.twilio_phone_number,
            twiml_url,
            status_callback_url,
        )
        call = client.calls.create(
            to=destination_number,
            from_=settings.twilio_phone_number,
            url=twiml_url,
            status_callback=status_callback_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
        )
        logger.info(
            "start_voice_node: Twilio call created session_id=%s call_sid=%s status=%s",
            state.session_id,
            call.sid,
            getattr(call, "status", None),
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
    # Renew the session lock at the start of each turn so it doesn't expire
    # during long listen phases (hold music, vendor pauses, etc.).
    manager = get_lock_manager()
    if not manager.renew(state.hotel_target.hotel_id, state.session_id):
        logger.warning(
            "listen_node: lock renewal failed for hotel %s session %s, terminating",
            state.hotel_target.hotel_id, state.session_id,
        )
        return {"status": SessionStatus.FAILED, "error_log": state.error_log + ["lock expired"]}
    # VoicePipeline drives the listen/respond cycle via handle_media_stream_connected()
    logger.info(
        "listen_node: awaiting media stream session_id=%s hotel_id=%s call_sid=%s status=%s",
        state.session_id,
        state.hotel_target.hotel_id,
        state.call_sid,
        state.status,
    )
    from app.orchestration.worker_graph import get_active_worker

    worker = get_active_worker(state.session_id)
    if worker is None:
        logger.warning("listen_node: no active worker found for session %s", state.session_id)
        return {"status": SessionStatus.FAILED, "error_log": state.error_log + ["worker missing"]}

    await worker.wait_for_call_end()
    logger.info(
        "listen_node: call ended session_id=%s hotel_id=%s transcript_turns=%d quotes=%d outcome=%s",
        state.session_id,
        state.hotel_target.hotel_id,
        len(state.transcript),
        len(state.quotes_received),
        state.outcome,
    )

    # Hang up the Twilio call
    if state.call_sid:
        try:
            from twilio.rest import Client as TwilioClient
            client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
            client.calls(state.call_sid).update(status="completed")
            logger.info("listen_node: hung up call session_id=%s call_sid=%s", state.session_id, state.call_sid)
        except Exception as exc:
            logger.warning("listen_node: failed to hang up call %s: %s", state.call_sid, exc)

    return {"status": SessionStatus.COMPLETED}


async def extract_facts_node(state: WorkerSessionState) -> dict:
    # Called from within VoicePipeline._process_utterance directly
    return {}


async def sync_quote_node(state: WorkerSessionState) -> dict:
    if not state.quotes_received:
        return {}
    latest = state.quotes_received[-1]
    client = get_http_client()
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
        client = get_http_client()
        resp = await client.get(_backend_url("/api/market/state"))
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
    manager = get_lock_manager()
    if not manager.renew(state.hotel_target.hotel_id, state.session_id):
        logger.warning(
            "check_terminate: lock renewal failed for hotel %s session %s, terminating",
            state.hotel_target.hotel_id, state.session_id,
        )
        return {"status": SessionStatus.FAILED, "error_log": state.error_log + ["lock expired"]}
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
    # If the lock was lost, only mark the session as failed and emit the
    # event — skip analysis, summaries, and memory writes that could
    # conflict with a replacement worker that now owns this hotel.
    if "lock expired" in state.error_log:
        logger.info("post_call_node: lock lost for session %s, marking failed", state.session_id)
        client = get_http_client()
        try:
            await client.patch(_backend_url(f"/api/sessions/{state.session_id}"), json={
                "status": SessionStatus.FAILED,
                "outcome": NegotiationOutcome.FAILED,
            })
        except Exception as exc:
            logger.warning("post_call_node: failed to patch session status: %s", exc)
        await get_event_bus().publish(WorkerEvent(
            event_type=EventType.WORKER_FAILED,
            session_id=state.session_id,
            campaign_id=state.campaign_id,
            hotel_id=state.hotel_target.hotel_id,
            payload={"reason": "lock expired"},
        ))
        return {"status": SessionStatus.FAILED, "outcome": NegotiationOutcome.FAILED}

    from app.llm.post_call_analyzer import analyze_call
    from app.memory.behavioral_store import get_behavioral_store

    try:
        analysis = await analyze_call(state)

        client = get_http_client()
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
            market=state.hotel_target.market_context.get("market", ""),
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
    if "lock expired" in state.error_log:
        return {}

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

from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import get_settings
from app.core.shared_clients import get_http_client
from app.core.events import EventType, WorkerEvent, get_event_bus
from app.artifacts.schemas import EmailAttachment
from app.email.contract_artifacts import (
    build_contract_artifacts,
    build_receipt_email_body,
    build_receipt_email_subject,
    resolve_receipt_recipient,
)
from app.email.mailgun_provider import MailgunProvider
from app.email.schemas import OutboundEmail
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

    # Mutate the original state object directly so the VoicePipeline
    # (which holds a reference to this same object) sees the data.
    state.behavioral_priors["market_brief"] = market_brief

    return {
        "behavioral_priors": {
            "market_brief": market_brief,
        }
    }


async def load_memory_node(state: WorkerSessionState) -> dict:
    from app.memory.behavioral_store import get_behavioral_store
    store = get_behavioral_store()
    profile = await store.load_priors(state.hotel_target.hotel_id)
    # Mutate the original state object directly so the VoicePipeline sees it.
    state.behavioral_priors["negotiation_summary"] = profile.to_prompt_context()

    merged = dict(state.behavioral_priors)
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
        galileo_agent_id = state.hotel_target.market_context.get("galileo_agent_id")
        if isinstance(galileo_agent_id, str) and galileo_agent_id.strip():
            try:
                from app.galileo.database import mark_agent_dialing

                await mark_agent_dialing(galileo_agent_id, call.sid)
            except Exception:
                logger.exception("Failed to sync start state to Galileo agent %s", galileo_agent_id)
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
            _settings = get_settings()
            client = TwilioClient(_settings.twilio_account_sid, _settings.twilio_auth_token)
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


_OUTCOME_TO_GALILEO_STATUS: dict[str, str] = {
    NegotiationOutcome.RATE_CONFIRMED: "Completed",
    NegotiationOutcome.CALLBACK_REQUESTED: "Awaiting Callback",
    NegotiationOutcome.NO_AVAILABILITY: "Completed",
    NegotiationOutcome.ESCALATED_TO_HUMAN: "Reviewing",
    NegotiationOutcome.FAILED: "Failed",
    NegotiationOutcome.TIMED_OUT: "Failed",
}


async def _update_galileo_agent(
    agent_id: str,
    outcome: str | NegotiationOutcome,
    best_rate: float | None,
) -> None:
    """Persist call result to the galileo_agents table so the frontend sees it."""
    from app.galileo.database import update_agent_call_result

    status = _OUTCOME_TO_GALILEO_STATUS.get(str(outcome), "Completed")
    try:
        await update_agent_call_result(
            agent_id=agent_id,
            status=status,
            outcome=str(outcome),
            current_price=best_rate,
        )
    except Exception as exc:
        logger.warning("_update_galileo_agent: failed for agent %s: %s", agent_id, exc)


async def post_call_node(state: WorkerSessionState) -> dict:
    # If the lock was lost, only mark the session as failed and emit the
    # event — skip analysis, summaries, and memory writes that could
    # conflict with a replacement worker that now owns this hotel.
    galileo_agent_id = state.hotel_target.market_context.get("galileo_agent_id")

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

        if galileo_agent_id:
            await _update_galileo_agent(galileo_agent_id, NegotiationOutcome.FAILED, None)

        await get_event_bus().publish(WorkerEvent(
            event_type=EventType.WORKER_FAILED,
            session_id=state.session_id,
            campaign_id=state.campaign_id,
            hotel_id=state.hotel_target.hotel_id,
            payload={"reason": "lock expired", "galileo_agent_id": galileo_agent_id},
        ))
        return {"status": SessionStatus.FAILED, "outcome": NegotiationOutcome.FAILED}

    from app.llm.post_call_analyzer import analyze_call
    from app.memory.behavioral_store import get_behavioral_store

    try:
        analysis = await analyze_call(state)
        state.outcome = analysis.outcome
        state.report_summary = analysis.summary
        receipt_recipient = resolve_receipt_recipient(state)
        if receipt_recipient:
            try:
                artifacts = await build_contract_artifacts(state)
                await _send_voice_receipt_email(state, receipt_recipient, artifacts.pdf_path)
                if state.receipt_artifacts is not None:
                    state.receipt_artifacts.email_sent_to = receipt_recipient
            except Exception as exc:
                logger.warning("voice receipt send failed for %s: %s", state.session_id, exc)
                state.error_log.append(f"receipt_send_failed: {exc}")

        # Prefer the deterministic outcome set by the pipeline over the LLM's guess.
        # The pipeline derives outcome from actual moves (ACCEPT -> RATE_CONFIRMED, etc.)
        # Only fall back to the LLM outcome if the pipeline didn't set one.
        final_outcome = state.outcome if state.outcome is not None else analysis.outcome
        # If pipeline says RATE_CONFIRMED (agent explicitly accepted), trust it
        # even if the LLM disagrees.
        if state.outcome == NegotiationOutcome.RATE_CONFIRMED:
            final_outcome = NegotiationOutcome.RATE_CONFIRMED
        analysis.outcome = final_outcome

        client = get_http_client()
        await client.patch(_backend_url(f"/api/sessions/{state.session_id}"), json={
            "status": SessionStatus.COMPLETED,
            "outcome": final_outcome,
            "transcript": state.transcript,
            "summary": analysis.summary,
            "key_patterns": analysis.key_patterns,
            "lessons": analysis.lessons,
            "call_quality_score": analysis.call_quality_score,
            "follow_up_recommended": analysis.follow_up_recommended,
            "follow_up_reason": analysis.follow_up_reason,
            "contract_details": state.contract_details.model_dump() if state.contract_details else None,
            "receipt_artifacts": state.receipt_artifacts.model_dump() if state.receipt_artifacts else None,
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

        if galileo_agent_id:
            await _update_galileo_agent(galileo_agent_id, analysis.outcome, best_rate)

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
            payload={"outcome": analysis.outcome, "best_rate": best_rate, "galileo_agent_id": galileo_agent_id},
        ))

        return {"status": SessionStatus.COMPLETED, "outcome": analysis.outcome}
    except Exception as exc:
        logger.exception("post_call_node failed: %s", exc)
        if galileo_agent_id:
            await _update_galileo_agent(galileo_agent_id, NegotiationOutcome.FAILED, None)

        await get_event_bus().publish(WorkerEvent(
            event_type=EventType.WORKER_FAILED,
            session_id=state.session_id,
            campaign_id=state.campaign_id,
            hotel_id=state.hotel_target.hotel_id,
            payload={"reason": str(exc), "galileo_agent_id": galileo_agent_id},
        ))
        return {"status": SessionStatus.COMPLETED, "outcome": NegotiationOutcome.FAILED}


async def _send_voice_receipt_email(state: WorkerSessionState, recipient: str, pdf_path: str) -> None:
    if state.contract_details is None:
        logger.warning("voice_receipt_email: session_id=%s skipped because contract_details is missing", state.session_id)
        return

    pdf_file = Path(pdf_path)
    provider = MailgunProvider()
    outbound = OutboundEmail(
        to_address=recipient,
        subject=build_receipt_email_subject(
            state.contract_details,
            state.outcome.value if state.outcome else None,
        ),
        text=build_receipt_email_body(
            state.contract_details,
            session_state=state,
            outcome=state.outcome.value if state.outcome else None,
        ),
        metadata={
            "session_id": state.session_id,
            "channel": "voice",
            "artifact_type": "negotiation_report",
        },
        attachments=[
            EmailAttachment(
                filename=pdf_file.name,
                content_type="application/pdf",
                data=pdf_file.read_bytes(),
            )
        ],
    )
    logger.info(
        "voice_receipt_email: session_id=%s recipient=%s subject=%s pdf_path=%s attachments=%d",
        state.session_id,
        recipient,
        outbound.subject,
        pdf_path,
        len(outbound.attachments),
    )
    receipt = await provider.send_message(outbound)
    logger.info(
        "voice_receipt_email: session_id=%s provider=%s provider_message_id=%s",
        state.session_id,
        receipt.provider,
        receipt.provider_message_id,
    )


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

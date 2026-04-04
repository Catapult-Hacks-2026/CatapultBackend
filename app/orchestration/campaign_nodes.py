from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import httpx

from app.core.config import get_settings
from app.hotel.enums import NegotiationOutcome, SessionStatus  # noqa: F401 (SessionStatus used in spawned worker state)
from app.hotel.schemas import HotelTarget, WorkerResult, WorkerSessionState
from app.memory.behavioral_store import get_behavioral_store
from app.orchestration.job_scorer import score_job_priority
from app.orchestration.session_lock import get_lock_manager

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2


def _backend_url(path: str) -> str:
    return f"{get_settings().base_url}{path}"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

async def ingest_targets_node(state: dict) -> dict:
    campaign_id = state["campaign_id"]
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(_backend_url(f"/api/campaigns/{campaign_id}/targets"))
            raw = resp.json() if resp.status_code == 200 else []
        except Exception as exc:
            logger.error("ingest_targets: fetch failed: %s", exc)
            raw = []

    target_jobs: dict[str, HotelTarget] = {}
    for item in raw:
        try:
            t = HotelTarget(**item)
            target_jobs[t.hotel_id] = t
        except Exception:
            logger.warning("ingest_targets: skipping malformed target: %s", item)

    existing: dict[str, HotelTarget] = state.get("target_jobs", {})
    existing.update(target_jobs)

    return {
        "target_jobs": existing,
        "queued_jobs": [(0.0, t) for t in existing.values()],
    }


# ---------------------------------------------------------------------------
# Deduplicate
# ---------------------------------------------------------------------------

def _dates_overlap(a_in: str, a_out: str, b_in: str, b_out: str) -> bool:
    try:
        return a_in < b_out and b_in < a_out
    except Exception:
        return False


async def deduplicate_node(state: dict) -> dict:
    queued: list[tuple[float, HotelTarget]] = state.get("queued_jobs", [])
    seen: dict[str, HotelTarget] = {}
    deduped: list[tuple[float, HotelTarget]] = []

    for score, target in queued:
        key = target.hotel_id
        if key in seen:
            existing = seen[key]
            if _dates_overlap(
                target.check_in, target.check_out,
                existing.check_in, existing.check_out,
            ):
                # Keep the one with the lower target_rate (more aggressive)
                if target.target_rate < existing.target_rate:
                    seen[key] = target
                    deduped = [(s, t) for s, t in deduped if t.hotel_id != key]
                    deduped.append((score, target))
                continue
        seen[key] = target
        deduped.append((score, target))

    return {"queued_jobs": deduped}


# ---------------------------------------------------------------------------
# Market state
# ---------------------------------------------------------------------------

async def load_market_state_node(state: dict) -> dict:
    async with httpx.AsyncClient() as client:
        market: dict[str, Any] = {}
        signals: dict[str, Any] = {}
        try:
            r = await client.get(_backend_url("/api/market/state"))
            if r.status_code == 200:
                market = r.json()
        except Exception as exc:
            logger.warning("load_market_state: /api/market/state failed: %s", exc)
        try:
            r = await client.get(_backend_url("/api/market/signals"))
            if r.status_code == 200:
                signals = r.json()
        except Exception as exc:
            logger.warning("load_market_state: /api/market/signals failed: %s", exc)

    return {"market_state": {**market, "signals": signals}}


# ---------------------------------------------------------------------------
# Score and prioritize
# ---------------------------------------------------------------------------

async def score_and_prioritize_node(state: dict) -> dict:
    queued: list[tuple[float, HotelTarget]] = state.get("queued_jobs", [])
    market_state: dict = state.get("market_state", {})
    signals: dict = market_state.get("signals", {})
    store = get_behavioral_store()

    scored: list[tuple[float, HotelTarget]] = []
    for _, target in queued:
        profile = await store.load_priors(target.hotel_id)
        hotel_signals = signals.get(target.hotel_id, {})
        priority = score_job_priority(target, hotel_signals, profile)
        scored.append((priority, target))

    scored.sort(key=lambda x: x[0], reverse=True)
    return {"queued_jobs": scored}


# ---------------------------------------------------------------------------
# Check eligibility
# ---------------------------------------------------------------------------

async def check_eligibility_node(state: dict) -> dict:
    from app.core.events import EventType, WorkerEvent, get_event_bus

    queued: list[tuple[float, HotelTarget]] = state.get("queued_jobs", [])
    active_workers: dict[str, str] = state.get("active_workers", {})
    market_state: dict = state.get("market_state", {})
    lock_manager = get_lock_manager()
    settings = get_settings()

    confirmed_quotes: dict = market_state.get("confirmed_quotes", {})
    concurrency_remaining = settings.worker_concurrency_limit - len(active_workers)
    eligible: list[tuple[float, HotelTarget]] = []
    deferred: list[tuple[float, HotelTarget]] = []

    for score, target in queued:
        # Duplicate check 1: another session holds the lock for this hotel
        if lock_manager.is_locked(target.hotel_id):
            deferred.append((score, target))
            continue

        # Duplicate check 2: a confirmed quote already exists within acceptable range
        confirmed = confirmed_quotes.get(target.hotel_id)
        if confirmed is not None:
            confirmed_rate = float(confirmed.get("nightly_rate", 0))
            if confirmed_rate > 0 and confirmed_rate <= target.max_rate:
                logger.info(
                    "check_eligibility: skipping hotel %s — confirmed rate $%.2f already on record",
                    target.hotel_id, confirmed_rate,
                )
                await get_event_bus().publish(WorkerEvent(
                    event_type=EventType.DUPLICATE_DETECTED,
                    session_id="",
                    campaign_id=state.get("campaign_id", ""),
                    hotel_id=target.hotel_id,
                    payload={"confirmed_rate": confirmed_rate, "reason": "pre_spawn_duplicate"},
                ))
                continue  # skip entirely — don't defer, just drop

        # Concurrency cap
        if concurrency_remaining <= 0:
            deferred.append((score, target))
            continue

        eligible.append((score, target))
        concurrency_remaining -= 1

    return {
        "queued_jobs": eligible,
        "_deferred_jobs": deferred,
    }


# ---------------------------------------------------------------------------
# Spawn workers
# ---------------------------------------------------------------------------

# Registry of asyncio Tasks keyed by session_id
_worker_tasks: dict[str, asyncio.Task] = {}


async def spawn_workers_node(state: dict) -> dict:
    from app.orchestration.worker_graph import build_worker_graph, register_worker, WorkerSession

    queued: list[tuple[float, HotelTarget]] = state.get("queued_jobs", [])
    active_workers: dict[str, str] = dict(state.get("active_workers", {}))
    campaign_id = state["campaign_id"]

    for _, target in queued:
        session_id = str(uuid.uuid4())
        initial_state = WorkerSessionState(
            session_id=session_id,
            campaign_id=campaign_id,
            hotel_target=target,
        )
        session = WorkerSession(initial_state)
        register_worker(session)

        task = asyncio.create_task(session.run(), name=f"worker-{session_id}")
        _worker_tasks[session_id] = task
        active_workers[session_id] = SessionStatus.INITIALIZING
        logger.info("Spawned worker %s for hotel %s", session_id, target.hotel_id)

    # queued_jobs consumed — workers are now active
    return {
        "active_workers": active_workers,
        "queued_jobs": [],
    }


# ---------------------------------------------------------------------------
# Monitor workers
# ---------------------------------------------------------------------------

async def monitor_workers_node(state: dict) -> dict:
    active_workers: dict[str, str] = dict(state.get("active_workers", {}))
    completed_jobs: list[WorkerResult] = list(state.get("completed_jobs", []))
    failed_jobs: list[WorkerResult] = list(state.get("failed_jobs", []))
    retry_queue: list[tuple[HotelTarget, int]] = list(state.get("retry_queue", []))

    still_active: dict[str, str] = {}

    for session_id, status in active_workers.items():
        task = _worker_tasks.get(session_id)
        if task is None or not task.done():
            still_active[session_id] = status
            continue

        _worker_tasks.pop(session_id, None)
        exc = task.exception()
        if exc:
            logger.error("Worker %s raised exception: %s", session_id, exc)
            # Will be routed in handle_outcomes_node
            failed_jobs.append(WorkerResult(
                session_id=session_id,
                status=SessionStatus.FAILED,
                outcome=NegotiationOutcome.FAILED,
                best_quote=None,
                transcript=[],
                moves_made=[],
            ))
        else:
            result: WorkerSessionState = task.result()
            best = min(result.quotes_received, key=lambda q: q.nightly_rate, default=None)
            worker_result = WorkerResult(
                session_id=result.session_id,
                status=result.status,
                outcome=result.outcome,
                best_quote=best,
                transcript=result.transcript,
                moves_made=result.moves_made,
            )
            if result.outcome == NegotiationOutcome.RATE_CONFIRMED:
                completed_jobs.append(worker_result)
            elif result.outcome == NegotiationOutcome.CALLBACK_REQUESTED:
                # Put back in retry queue with delay handled by eligibility check
                retry_queue.append((result.hotel_target, 0))
            else:
                failed_jobs.append(worker_result)

    return {
        "active_workers": still_active,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "retry_queue": retry_queue,
    }


# ---------------------------------------------------------------------------
# Handle outcomes
# ---------------------------------------------------------------------------

async def handle_outcomes_node(state: dict) -> dict:
    retry_queue: list[tuple[HotelTarget, int]] = list(state.get("retry_queue", []))
    failed_jobs: list[WorkerResult] = list(state.get("failed_jobs", []))
    deferred: list[tuple[float, HotelTarget]] = list(state.get("_deferred_jobs", []))

    promotable: list[tuple[float, HotelTarget]] = []
    exhausted: list[WorkerResult] = []

    for target, retry_count in retry_queue:
        if retry_count < _MAX_RETRIES:
            promotable.append((0.5, target))  # re-score next cycle
        else:
            exhausted.append(WorkerResult(
                session_id="",
                status=SessionStatus.FAILED,
                outcome=NegotiationOutcome.FAILED,
                best_quote=None,
                transcript=[],
                moves_made=[],
            ))

    # Merge deferred back into queue for next cycle
    next_queue = deferred + promotable

    return {
        "queued_jobs": next_queue,
        "retry_queue": [],
        "failed_jobs": failed_jobs + exhausted,
    }


def route_campaign(state: dict) -> str:
    queued = state.get("queued_jobs", [])
    active = state.get("active_workers", {})
    retry = state.get("retry_queue", [])
    deferred = state.get("_deferred_jobs", [])
    if queued or active or retry or deferred:
        return "continue"
    return "done"


# ---------------------------------------------------------------------------
# Emit summary
# ---------------------------------------------------------------------------

async def emit_summary_node(state: dict) -> dict:
    campaign_id = state["campaign_id"]
    completed = state.get("completed_jobs", [])
    failed = state.get("failed_jobs", [])

    summary = {
        "campaign_id": campaign_id,
        "total_completed": len(completed),
        "total_failed": len(failed),
        "best_rates": [
            {"hotel_id": r.best_quote and r.best_quote.nightly_rate}
            for r in completed if r.best_quote
        ],
    }

    async with httpx.AsyncClient() as client:
        try:
            await client.post(_backend_url(f"/api/campaigns/{campaign_id}/summary"), json=summary)
        except Exception as exc:
            logger.error("emit_summary: POST failed: %s", exc)

    return {"system_status": "done"}

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from app.core.database import get_db
from app.llm.client import invoke_json
from app.llm.prompts import POST_CALL_ANALYSIS_SYSTEM
from app.models.schemas import BuyerConfig, VendorOffer
from app.orchestration.session_lock import get_lock_manager
from app.services.memory import (
    extract_memory_candidates,
    load_negotiation_working_memory_from_redis,
    load_vendor_priors,
    refresh_negotiation_working_memory,
    store_memory_candidates,
)
from app.services.rag import add_negotiation_to_history, retrieve_vendor_context
from app.services.research import generate_negotiation_brief
from app.services.scoring import score_offer
from app.services.voice import initiate_call

logger = logging.getLogger(__name__)


async def load_context_node(state: dict) -> dict:
    conn = get_db()
    row = conn.execute(
        "SELECT vendor_name, product_category, config, research_brief, current_offer, utility_score, status FROM negotiations WHERE id = ?",
        (state["negotiation_id"],),
    ).fetchone()
    messages = conn.execute(
        """
        SELECT role, content, structured_data, created_at
        FROM messages
        WHERE negotiation_id = ?
        ORDER BY created_at, id
        """,
        (state["negotiation_id"],),
    ).fetchall()
    conn.close()
    if row is None:
        raise ValueError(f"Negotiation {state['negotiation_id']} not found")

    research_brief = json.loads(row["research_brief"]) if row["research_brief"] else None
    if research_brief is None:
        try:
            research_brief = generate_negotiation_brief(state["negotiation_id"])
        except Exception:
            logger.exception("Failed to generate research brief for %s", state["negotiation_id"])

    latest_offer = json.loads(row["current_offer"]) if row["current_offer"] else None
    transcript = [{"role": message["role"], "content": message["content"]} for message in messages]
    return {
        "vendor_name": row["vendor_name"],
        "product_category": row["product_category"],
        "buyer_config": json.loads(row["config"]),
        "research_brief": research_brief,
        "latest_offer": latest_offer,
        "transcript": transcript,
        "transcript_window": [f"{turn['role']}: {turn['content']}" for turn in transcript[-12:]],
        "rag_context": retrieve_vendor_context(
            row["vendor_name"],
            json.dumps(latest_offer or {"product_category": row["product_category"]}),
            top_k=5,
        ),
        "session_status": row["status"],
        "debug_log": state.get("debug_log", []) + ["load_context_node complete"],
    }

async def load_memory_node(state: dict) -> dict:
    priors = load_vendor_priors(state["vendor_name"], state["product_category"])
    working_memory = load_negotiation_working_memory_from_redis(state["negotiation_id"])
    return {
        "vendor_priors": priors,
        "working_memory": working_memory,
        "debug_log": state.get("debug_log", []) + ["load_memory_node complete"],
    }


async def acquire_lock_node(state: dict) -> dict:
    acquired = await get_lock_manager().acquire(state["negotiation_id"], state["negotiation_id"])
    return {
        "lock_acquired": acquired,
        "debug_log": state.get("debug_log", []) + [f"acquire_lock_node acquired={acquired}"],
    }


def route_lock(state: dict) -> str:
    return "acquired" if state.get("lock_acquired") else "locked"


async def start_voice_node(state: dict) -> dict:
    result = initiate_call(state["negotiation_id"], state["vendor_phone"])
    return {
        "call_sid": result.get("call_sid"),
        "session_status": result.get("status", "ringing"),
        "debug_log": state.get("debug_log", []) + ["start_voice_node complete"],
    }


async def listen_node(state: dict) -> dict:
    from app.orchestration.worker_graph import get_active_worker

    worker = get_active_worker(state["negotiation_id"])
    if worker is None:
        raise RuntimeError(f"Worker {state['negotiation_id']} is not registered")
    await worker.wait_for_call_end()
    snapshot = worker.snapshot()
    snapshot["debug_log"] = snapshot.get("debug_log", []) + ["listen_node complete"]
    return snapshot


async def extract_facts_node(state: dict) -> dict:
    return {"debug_log": state.get("debug_log", []) + ["extract_facts_node marker"]}


async def sync_quote_node(state: dict) -> dict:
    latest_offer = state.get("latest_offer")
    extracted_facts = state.get("extracted_facts")
    if not latest_offer:
        return {"debug_log": state.get("debug_log", []) + ["sync_quote_node skipped"]}

    conn = get_db()
    conn.execute(
        """
        INSERT INTO quote_events (negotiation_id, vendor_name, product_category, extracted_facts, offer)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            state["negotiation_id"],
            state["vendor_name"],
            state["product_category"],
            json.dumps(extracted_facts) if extracted_facts else None,
            json.dumps(latest_offer),
        ),
    )
    utility_score = state.get("scoring_breakdown", {}).get("total_utility")
    try:
        utility_score = score_offer(
            VendorOffer.model_validate(latest_offer),
            BuyerConfig.model_validate(state["buyer_config"]),
        ).total_utility
    except Exception:
        logger.exception("Failed to score latest offer for %s", state["negotiation_id"])
    conn.execute(
        """
        INSERT INTO latest_quotes (negotiation_id, vendor_name, product_category, extracted_facts, offer, utility_score, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(negotiation_id) DO UPDATE SET
            vendor_name = excluded.vendor_name,
            product_category = excluded.product_category,
            extracted_facts = excluded.extracted_facts,
            offer = excluded.offer,
            utility_score = excluded.utility_score,
            updated_at = excluded.updated_at
        """,
        (
            state["negotiation_id"],
            state["vendor_name"],
            state["product_category"],
            json.dumps(extracted_facts) if extracted_facts else None,
            json.dumps(latest_offer),
            utility_score,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return {"debug_log": state.get("debug_log", []) + ["sync_quote_node complete"]}


async def check_cross_session_node(state: dict) -> dict:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT vendor_name, offer, utility_score, updated_at
        FROM latest_quotes
        WHERE negotiation_id != ?
          AND product_category = ?
        ORDER BY utility_score DESC, updated_at DESC
        LIMIT 3
        """,
        (state["negotiation_id"], state["product_category"]),
    ).fetchall()
    conn.close()
    competing_offers = [
        {
            "vendor_name": row["vendor_name"],
            "offer": json.loads(row["offer"]) if row["offer"] else None,
            "utility_score": row["utility_score"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]
    return {
        "competing_offers": competing_offers,
        "debug_log": state.get("debug_log", []) + ["check_cross_session_node complete"],
    }


async def decide_move_node(state: dict) -> dict:
    return {"debug_log": state.get("debug_log", []) + ["decide_move_node marker"]}


async def speak_node(state: dict) -> dict:
    return {"debug_log": state.get("debug_log", []) + ["speak_node marker"]}


async def check_terminate_node(state: dict) -> dict:
    status = state.get("final_outcome") or state.get("session_status") or "completed"
    return {
        "continue_call": False,
        "session_status": status,
        "debug_log": state.get("debug_log", []) + [f"check_terminate_node status={status}"],
    }


def route_terminate(state: dict) -> str:
    return "continue" if state.get("continue_call") else "done"


async def post_call_node(state: dict) -> dict:
    transcript = state.get("transcript", [])
    transcript_text = "\n".join(f"{turn['role']}: {turn['content']}" for turn in transcript)
    best_offer = state.get("latest_offer")
    analysis = {
        "summary": "Call completed without post-call analysis.",
        "outcome": state.get("final_outcome") or state.get("session_status") or "completed",
        "key_patterns": [],
        "best_offer": best_offer,
        "lessons": [],
    }
    if transcript_text and state.get("buyer_config"):
        try:
            analysis = invoke_json(
                POST_CALL_ANALYSIS_SYSTEM,
                json.dumps(
                    {
                        "vendor_name": state["vendor_name"],
                        "product_category": state["product_category"],
                        "transcript": transcript_text,
                        "latest_offer": best_offer,
                    }
                ),
                model=None,
                max_tokens=900,
                temperature=0,
            )
        except Exception:
            logger.exception("post_call_node analysis failed for %s", state["negotiation_id"])

    conn = get_db()
    negotiation = conn.execute(
        "SELECT current_offer, status FROM negotiations WHERE id = ?",
        (state["negotiation_id"],),
    ).fetchone()
    messages = conn.execute(
        "SELECT role, content, structured_data, utility_score, rag_context, guardrail_log, created_at FROM messages WHERE negotiation_id = ? ORDER BY created_at, id",
        (state["negotiation_id"],),
    ).fetchall()
    conn.close()

    message_payload = []
    for message in messages:
        message_payload.append(
            {
                "role": message["role"],
                "content": message["content"],
                "structured_data": json.loads(message["structured_data"]) if message["structured_data"] else None,
                "utility_score": message["utility_score"],
                "rag_context": json.loads(message["rag_context"]) if message["rag_context"] else None,
                "guardrail_log": json.loads(message["guardrail_log"]) if message["guardrail_log"] else None,
                "created_at": message["created_at"],
            }
        )
    add_negotiation_to_history(
        negotiation_id=state["negotiation_id"],
        vendor_name=state["vendor_name"],
        product_category=state["product_category"],
        messages=message_payload,
        final_offer=json.loads(negotiation["current_offer"]) if negotiation and negotiation["current_offer"] else best_offer,
        outcome=analysis.get("outcome", state.get("session_status", "completed")),
    )
    return {
        "final_outcome": analysis.get("outcome", state.get("session_status")),
        "debug_log": state.get("debug_log", []) + ["post_call_node complete"],
    }


async def emit_memory_node(state: dict) -> dict:
    candidates = extract_memory_candidates(
        state["vendor_name"],
        state.get("transcript", []),
        state.get("latest_offer"),
    )
    store_memory_candidates(candidates)
    working_memory = refresh_negotiation_working_memory(state["negotiation_id"])
    return {
        "memory_candidates": candidates,
        "working_memory": working_memory,
        "debug_log": state.get("debug_log", []) + ["emit_memory_node complete"],
    }

async def release_lock_node(state: dict) -> dict:
    get_lock_manager().release(state["negotiation_id"], state["negotiation_id"])
    return {
        "lock_acquired": False,
        "debug_log": state.get("debug_log", []) + ["release_lock_node complete"],
    }

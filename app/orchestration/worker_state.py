from __future__ import annotations

from typing import Any, TypedDict


class WorkerSessionState(TypedDict, total=False):
    negotiation_id: str
    vendor_name: str
    vendor_phone: str
    product_category: str
    campaign_id: str | None
    buyer_config: dict[str, Any]
    session_status: str
    transcript_window: list[str]
    transcript: list[dict[str, Any]]
    latest_offer: dict[str, Any] | None
    extracted_facts: dict[str, Any] | None
    vendor_priors: dict[str, Any] | None
    rag_context: list[dict[str, Any]]
    research_brief: dict[str, Any] | None
    scoring_breakdown: dict[str, Any] | None
    objections_used: list[str]
    manager_reached: bool
    callback_requested: bool
    continue_call: bool
    next_action: str | None
    final_outcome: str | None
    call_sid: str | None
    lock_acquired: bool
    call_completed: bool
    memory_candidates: list[dict[str, Any]]
    competing_offers: list[dict[str, Any]]
    working_memory: list[dict[str, Any]]
    last_agent_message: str | None
    debug_log: list[str]


def build_worker_state(
    *,
    negotiation_id: str,
    vendor_name: str,
    vendor_phone: str,
    product_category: str,
    buyer_config: dict[str, Any],
    campaign_id: str | None = None,
) -> WorkerSessionState:
    return {
        "negotiation_id": negotiation_id,
        "vendor_name": vendor_name,
        "vendor_phone": vendor_phone,
        "product_category": product_category,
        "campaign_id": campaign_id,
        "buyer_config": buyer_config,
        "session_status": "initializing",
        "transcript_window": [],
        "transcript": [],
        "latest_offer": None,
        "extracted_facts": None,
        "vendor_priors": None,
        "rag_context": [],
        "research_brief": None,
        "scoring_breakdown": None,
        "objections_used": [],
        "manager_reached": False,
        "callback_requested": False,
        "continue_call": True,
        "next_action": None,
        "final_outcome": None,
        "call_sid": None,
        "lock_acquired": False,
        "call_completed": False,
        "memory_candidates": [],
        "competing_offers": [],
        "working_memory": [],
        "last_agent_message": None,
        "debug_log": [],
    }

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.orchestration.campaign_nodes import (
    check_eligibility_node,
    deduplicate_node,
    emit_summary_node,
    handle_outcomes_node,
    ingest_targets_node,
    load_market_state_node,
    monitor_workers_node,
    route_campaign,
    score_and_prioritize_node,
    spawn_workers_node,
)


def build_campaign_graph() -> Any:
    g = StateGraph(dict)

    g.add_node("ingest_targets",       ingest_targets_node)
    g.add_node("deduplicate",          deduplicate_node)
    g.add_node("load_market_state",    load_market_state_node)
    g.add_node("score_and_prioritize", score_and_prioritize_node)
    g.add_node("check_eligibility",    check_eligibility_node)
    g.add_node("spawn_workers",        spawn_workers_node)
    g.add_node("monitor_workers",      monitor_workers_node)
    g.add_node("handle_outcomes",      handle_outcomes_node)
    g.add_node("emit_summary",         emit_summary_node)

    g.set_entry_point("ingest_targets")
    g.add_edge("ingest_targets",       "deduplicate")
    g.add_edge("deduplicate",          "load_market_state")
    g.add_edge("load_market_state",    "score_and_prioritize")
    g.add_edge("score_and_prioritize", "check_eligibility")
    g.add_edge("check_eligibility",    "spawn_workers")
    g.add_edge("spawn_workers",        "monitor_workers")
    g.add_edge("monitor_workers",      "handle_outcomes")
    g.add_conditional_edges("handle_outcomes", route_campaign, {
        "continue": "load_market_state",
        "done":     "emit_summary",
    })
    g.add_edge("emit_summary", END)

    return g.compile()


def make_initial_campaign_state(campaign_id: str) -> dict:
    return {
        "campaign_id": campaign_id,
        "target_jobs": {},
        "queued_jobs": [],
        "active_workers": {},
        "completed_jobs": [],
        "failed_jobs": [],
        "retry_queue": [],
        "_deferred_jobs": [],
        "market_state": {},
        "system_status": "running",
    }

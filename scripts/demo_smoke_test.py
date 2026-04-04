#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Union


DEFAULT_CONFIG = {
    "target_unit_price": 85,
    "max_unit_price": 100,
    "target_shipping_cost": 0,
    "max_shipping_cost": 200,
    "preferred_payment_terms": 60,
    "min_payment_terms": 30,
    "preferred_delivery_days": 14,
    "max_delivery_days": 30,
    "quantity": 1000,
    "weight_price": 0.45,
    "weight_shipping": 0.15,
    "weight_payment_terms": 0.20,
    "weight_delivery": 0.20,
    "min_acceptable_utility": 0.6,
}


JsonValue = Union[Dict[str, Any], List[Any]]


def request_json(method: str, url: str, payload: Optional[Dict[str, Any]] = None) -> JsonValue:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"[HTTP {exc.code}] {method} {url}", file=sys.stderr)
        print(error_body, file=sys.stderr)
        raise


def print_heading(title: str) -> None:
    print(f"\n=== {title} ===")


def summarize_negotiation(data: Dict[str, Any]) -> None:
    print(
        f"id={data['id']} vendor={data['vendor_name']} category={data['product_category']} "
        f"status={data['status']} round={data['round_number']} utility={data['utility_score']}"
    )


def summarize_turn(label: str, data: Dict[str, Any]) -> None:
    utility = data.get("scoring_breakdown", {}).get("total_utility")
    print(f"{label}: status={data.get('status')} utility={utility}")
    print(f"agent_message: {data.get('agent_message')}")
    action = data.get("agent_action") or {}
    if action.get("counter_offer"):
        print(f"counter_offer: {json.dumps(action['counter_offer'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a parsed demo smoke test against the Catapult backend.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    print_heading("Create Single Negotiation")
    created = request_json(
        "POST",
        f"{base_url}/negotiations/",
        {
            "vendor_name": "Acme Steel",
            "product_category": "steel coils",
            "strategy": "balanced",
            "config": DEFAULT_CONFIG,
        },
    )
    summarize_negotiation(created)
    negotiation_id = created["id"]

    print_heading("Fetch Negotiation Detail")
    detail = request_json("GET", f"{base_url}/negotiations/{negotiation_id}")
    negotiation = detail["negotiation"]
    summarize_negotiation(negotiation)
    brief = detail.get("research_brief")
    if brief:
        print(f"research_brief.summary: {brief.get('summary')}")
        print(f"research_brief.anchor: {json.dumps(brief.get('recommended_anchor'))}")
    else:
        print("research_brief.summary: <missing>")

    print_heading("Simulate Vendor Message")
    simulated = request_json(
        "POST",
        f"{base_url}/webhooks/simulate",
        {
            "vendor_name": "Acme Steel",
            "product_category": "steel coils",
            "message": "We can do 96 dollars per unit, 150 shipping, net 30, delivery in 21 days.",
        },
    )
    print(f"negotiation_id={simulated['negotiation_id']}")
    summarize_turn("simulate", simulated)

    print_heading("Create Batch Negotiations")
    batch = request_json(
        "POST",
        f"{base_url}/negotiations/batch",
        {
            "product_category": "steel coils",
            "auto_call": False,
            "config": DEFAULT_CONFIG,
            "vendors": [
                {"vendor_name": "Forge Supply", "strategy": "aggressive"},
                {"vendor_name": "Atlas Metals", "strategy": "balanced"},
            ],
        },
    )
    batch_ids: list[str] = []
    for index, item in enumerate(batch["items"], start=1):
        print(f"batch_item_{index}:")
        summarize_negotiation(item["negotiation"])
        batch_ids.append(item["negotiation"]["id"])

    print_heading("Run Vendor Turns")
    turn_one = request_json(
        "POST",
        f"{base_url}/webhooks/vendor",
        {
            "negotiation_id": batch_ids[0],
            "message": "We can offer 94 per unit, shipping 120, net 30, delivery 18 days.",
        },
    )
    summarize_turn("vendor_1_turn", turn_one)

    turn_two = request_json(
        "POST",
        f"{base_url}/webhooks/vendor",
        {
            "negotiation_id": batch_ids[1],
            "message": "Best we can do is 92 per unit, shipping 80, net 45, delivery 16 days.",
        },
    )
    summarize_turn("vendor_2_turn", turn_two)

    print_heading("Fetch Post-Turn Detail")
    for negotiation_id in batch_ids:
        detail = request_json("GET", f"{base_url}/negotiations/{negotiation_id}")
        summarize_negotiation(detail["negotiation"])
        print(f"messages={len(detail['messages'])}")
        brief = detail.get("research_brief")
        if brief:
            print(f"brief_source={brief.get('source')}")

    print_heading("Done")
    print("Smoke test completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

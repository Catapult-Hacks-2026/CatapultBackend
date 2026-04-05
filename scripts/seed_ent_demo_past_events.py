"""Seed past events and agents for the ent_demo enterprise.

Creates several completed past events under ent_demo and distributes
galileo_agents so that every company (cmp_lumina, cmp_apex, cmp_vanguard,
cmp_coastal, cmp_atlas, cmp_pinnacle) appears in more than one event.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


DB_PATH = Path("data/negotiations.db")
ENTERPRISE_ID = "ent_demo"


def _iso(base: datetime, minutes: int) -> str:
    return (base + timedelta(minutes=minutes)).replace(microsecond=0).isoformat() + "Z"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


EVENTS: list[dict[str, object]] = [
    {
        "id": "evt_demo_ny_summit_2024",
        "name": "NYC Leadership Summit",
        "location": "New York, NY",
        "start_date": "2024-03-15",
        "end_date": "2024-03-18",
        "attendees": 180,
        "service": "Hotel",
        "status": "Completed",
        "requirements": "Midtown conference hotel with executive meeting space.",
        "budget_per_person": 580.0,
    },
    {
        "id": "evt_demo_seattle_dev_2024",
        "name": "Seattle Developer Week",
        "location": "Seattle, WA",
        "start_date": "2024-06-10",
        "end_date": "2024-06-13",
        "attendees": 240,
        "service": "Hotel",
        "status": "Completed",
        "requirements": "Downtown blocks near convention center, high-speed WiFi.",
        "budget_per_person": 495.0,
    },
    {
        "id": "evt_demo_chi_board_2024",
        "name": "Chicago Board Offsite",
        "location": "Chicago, IL",
        "start_date": "2024-09-22",
        "end_date": "2024-09-25",
        "attendees": 95,
        "service": "Hotel",
        "status": "Completed",
        "requirements": "River North location with private dining and boardroom.",
        "budget_per_person": 640.0,
    },
    {
        "id": "evt_demo_austin_summit_2025",
        "name": "Austin Partner Summit",
        "location": "Austin, TX",
        "start_date": "2025-01-20",
        "end_date": "2025-01-23",
        "attendees": 310,
        "service": "Hotel",
        "status": "Completed",
        "requirements": "Downtown venue with large ballroom and breakout rooms.",
        "budget_per_person": 465.0,
    },
    {
        "id": "evt_demo_la_conf_2025",
        "name": "LA Customer Conference",
        "location": "Los Angeles, CA",
        "start_date": "2025-05-05",
        "end_date": "2025-05-08",
        "attendees": 520,
        "service": "Hotel",
        "status": "Completed",
        "requirements": "LA Live adjacency, preferred transcon fares, airport shuttle.",
        "budget_per_person": 735.0,
    },
    {
        "id": "evt_demo_boston_offsite_2025",
        "name": "Boston Engineering Offsite",
        "location": "Boston, MA",
        "start_date": "2025-08-12",
        "end_date": "2025-08-15",
        "attendees": 140,
        "service": "Hotel",
        "status": "Completed",
        "requirements": "Back Bay hotel with workshop spaces and late checkout.",
        "budget_per_person": 520.0,
    },
]


# Each tuple: (event_id, company_id, company_name, ideal, ceiling, market, current, accepted, outcome)
AGENTS: list[tuple[str, str, str, float, float, float, float, int, str]] = [
    # NYC Leadership Summit
    ("evt_demo_ny_summit_2024", "cmp_lumina", "Hilton Hotels", 245.0, 305.0, 318.0, 259.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_ny_summit_2024", "cmp_apex", "Marriott Bonvoy", 252.0, 312.0, 325.0, 268.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_ny_summit_2024", "cmp_coastal", "IHG Hotels", 228.0, 284.0, 296.0, 272.0, 0, "FAILED"),
    # Seattle Developer Week
    ("evt_demo_seattle_dev_2024", "cmp_vanguard", "Hyatt Hotels", 210.0, 262.0, 274.0, 219.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_seattle_dev_2024", "cmp_coastal", "IHG Hotels", 205.0, 258.0, 268.0, 214.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_seattle_dev_2024", "cmp_atlas", "Loews Hotels", 372.0, 475.0, 498.0, 389.0, 1, "RATE_CONFIRMED"),
    # Chicago Board Offsite
    ("evt_demo_chi_board_2024", "cmp_lumina", "Hilton Hotels", 268.0, 330.0, 344.0, 285.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_chi_board_2024", "cmp_vanguard", "Hyatt Hotels", 262.0, 325.0, 336.0, 309.0, 0, "ESCALATED_TO_HUMAN"),
    ("evt_demo_chi_board_2024", "cmp_pinnacle", "Peninsula Hotels", 455.0, 568.0, 592.0, 472.0, 1, "RATE_CONFIRMED"),
    # Austin Partner Summit
    ("evt_demo_austin_summit_2025", "cmp_apex", "Marriott Bonvoy", 218.0, 272.0, 284.0, 226.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_austin_summit_2025", "cmp_atlas", "Loews Hotels", 345.0, 440.0, 462.0, 358.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_austin_summit_2025", "cmp_pinnacle", "Peninsula Hotels", 420.0, 532.0, 556.0, 501.0, 0, "FAILED"),
    # LA Customer Conference
    ("evt_demo_la_conf_2025", "cmp_lumina", "Hilton Hotels", 288.0, 358.0, 372.0, 298.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_la_conf_2025", "cmp_coastal", "IHG Hotels", 255.0, 318.0, 330.0, 279.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_la_conf_2025", "cmp_atlas", "Loews Hotels", 398.0, 512.0, 534.0, 415.0, 1, "RATE_CONFIRMED"),
    # Boston Engineering Offsite
    ("evt_demo_boston_offsite_2025", "cmp_apex", "Marriott Bonvoy", 238.0, 298.0, 312.0, 285.0, 0, "ESCALATED_TO_HUMAN"),
    ("evt_demo_boston_offsite_2025", "cmp_vanguard", "Hyatt Hotels", 232.0, 288.0, 302.0, 244.0, 1, "RATE_CONFIRMED"),
    ("evt_demo_boston_offsite_2025", "cmp_pinnacle", "Peninsula Hotels", 432.0, 548.0, 572.0, 448.0, 1, "RATE_CONFIRMED"),
]


def _agent_id(event_id: str, company_id: str) -> str:
    # e.g. agt_demo_ny_summit_2024_lumina
    event_slug = event_id.replace("evt_demo_", "")
    company_slug = company_id.replace("cmp_", "")
    return f"agt_demo_{event_slug}_{company_slug}"


def _build_price_path(market: float, current: float) -> list[tuple[str, float, str, int]]:
    midpoint = round((market + current) / 2, 2)
    return [
        ("Market Baseline", market, "current", 0),
        ("Supplier Offer", round(market * 0.97, 2), "offer", 1),
        ("Galileo Counter", midpoint, "negotiated", 2),
        ("Final Position", current, "negotiated", 3),
    ]


def _build_activity(agent_id: str, market: float, current: float, accepted: int, started_at: datetime) -> list[tuple]:
    return [
        (f"act_{agent_id}_1", agent_id, market, "Queued", "neutral",
         "Agent queued and sourcing market comps.", "neutral", _iso(started_at, 0), 0),
        (f"act_{agent_id}_2", agent_id, round(market * 0.97, 2), "Supplier Reply", "neutral",
         "Supplier shared first revised rate.", "neutral", _iso(started_at, 12), 0),
        (f"act_{agent_id}_3", agent_id, round((market + current) / 2, 2), "Counter", "neutral",
         "Galileo submitted a counter aligned to guardrails.", "positive", _iso(started_at, 22), 0),
        (f"act_{agent_id}_4", agent_id, current, "Final",
         "savings" if accepted else "neutral",
         "Final position captured for review.",
         "positive" if accepted else "neutral", _iso(started_at, 34), 0),
    ]


def _build_messages(agent_id: str, ideal: float, current: float, started_at: datetime) -> list[tuple]:
    return [
        (f"msg_{agent_id}_1", agent_id,
         "Thanks for joining. We are benchmarking rates for this event block.",
         "Galileo", _iso(started_at, 1)),
        (f"msg_{agent_id}_2", agent_id,
         f"We can start at {round(current * 1.08, 2):.2f} with standard terms.",
         "Rep", _iso(started_at, 9)),
        (f"msg_{agent_id}_3", agent_id,
         f"We need to land near {ideal:.2f} to meet policy targets.",
         "Galileo", _iso(started_at, 17)),
        (f"msg_{agent_id}_4", agent_id,
         f"Understood. We can hold {current:.2f} with concessions.",
         "Rep", _iso(started_at, 26)),
    ]


def _build_previous(agent_id: str, event_id: str, current: float, market: float, accepted: int) -> tuple:
    savings = round((market - current) * 180, 2)
    return (
        f"prev_{agent_id}_1",
        agent_id,
        f"CTR-{agent_id[-6:].upper()}-24",
        "EMEA" if "london" in event_id.lower() else "North America",
        "12 months",
        round(current * 1.04, 2),
        savings if accepted else round(savings * 0.0, 2),
        "ACTIVE" if accepted else "ARCHIVED",
        "2024-02-01",
        "2025-01-31",
    )


def seed() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found at {DB_PATH}")

    conn = _connect()
    try:
        # Ensure enterprise exists
        row = conn.execute(
            "SELECT 1 FROM galileo_enterprises WHERE id = ? LIMIT 1",
            (ENTERPRISE_ID,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"Enterprise {ENTERPRISE_ID} not found in galileo_enterprises")

        # Insert events
        conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_events (
                id, enterprise_id, name, location, start_date, end_date,
                attendees, service, status, requirements, budget_per_person
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    e["id"], ENTERPRISE_ID, e["name"], e["location"],
                    e["start_date"], e["end_date"], e["attendees"],
                    e["service"], e["status"], e["requirements"],
                    e["budget_per_person"],
                )
                for e in EVENTS
            ],
        )

        # Insert agents + related rows
        agent_rows: list[tuple] = []
        price_points: list[tuple] = []
        activities: list[tuple] = []
        messages: list[tuple] = []
        previous: list[tuple] = []

        base = datetime(2024, 2, 1, 10, 0, 0, tzinfo=UTC)
        for idx, (event_id, company_id, company_name, ideal, ceiling, market, current, accepted, outcome) in enumerate(AGENTS):
            aid = _agent_id(event_id, company_id)
            started_at = base + timedelta(days=idx * 5)
            agent_rows.append(
                (aid, ENTERPRISE_ID, event_id, company_name, company_id,
                 "Completed" if outcome == "RATE_CONFIRMED" else ("Cancelled" if outcome == "FAILED" else "Completed"),
                 outcome, ideal, ceiling, market, current, accepted)
            )
            for label, price, ptype, rnd in _build_price_path(market, current):
                price_points.append((aid, label, price, ptype, rnd))
            activities.extend(_build_activity(aid, market, current, accepted, started_at))
            messages.extend(_build_messages(aid, ideal, current, started_at))
            previous.append(_build_previous(aid, event_id, current, market, accepted))

        conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_agents (
                id, enterprise_id, event_id, company_name, company_id, status,
                outcome, ideal_price, ceiling_price, market_price, current_price, is_accepted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            agent_rows,
        )

        conn.executemany(
            """
            INSERT INTO galileo_price_points (agent_id, label, price, type, round)
            VALUES (?, ?, ?, ?, ?)
            """,
            price_points,
        )

        conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            activities,
        )

        conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_messages (
                id, agent_id, message, sender, timestamp
            ) VALUES (?, ?, ?, ?, ?)
            """,
            messages,
        )

        conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_previous_negotiations (
                id, agent_id, contract_id, region, duration, final_rate, total_savings,
                status, start_date, end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            previous,
        )

        conn.commit()
        print(f"Inserted {len(EVENTS)} events and {len(agent_rows)} agents for {ENTERPRISE_ID}.")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import redis


DB_PATH = Path("data/negotiations.db")
REDIS_URL = "redis://localhost:6379/0"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _connect_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def seed_negotiation_new(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM negotiations WHERE id = ? LIMIT 1",
        ("new",),
    ).fetchone()
    if row is not None:
        return

    config = {
        "target_unit_price": 179.0,
        "max_unit_price": 239.0,
        "target_shipping_cost": 0.0,
        "max_shipping_cost": 0.0,
        "preferred_payment_terms": 45,
        "min_payment_terms": 30,
        "preferred_delivery_days": 14,
        "max_delivery_days": 30,
        "quantity": 25,
        "weight_price": 0.45,
        "weight_shipping": 0.15,
        "weight_payment_terms": 0.2,
        "weight_delivery": 0.2,
        "min_acceptable_utility": 0.6,
    }
    offer = {
        "unit_price": 212.0,
        "shipping_cost": 0.0,
        "payment_terms_days": 30,
        "delivery_days": 14,
        "notes": "Dummy record for frontend route probing",
    }

    conn.execute(
        """
        INSERT INTO negotiations (
            id, vendor_name, product_category, status, strategy, config,
            current_offer, research_brief, utility_score, round_number, max_rounds,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            "new",
            "Demo Hotel Vendor",
            "hotel",
            "pending",
            "balanced",
            json.dumps(config),
            json.dumps(offer),
            json.dumps(
                {
                    "summary": "Dummy negotiation created to satisfy GET /negotiations/new.",
                    "source": "scripts/seed_404_dummy_data.py",
                }
            ),
            0.58,
            1,
            10,
        ),
    )
    conn.execute(
        """
        INSERT INTO messages (
            negotiation_id, role, content, structured_data, utility_score, created_at
        )
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            "new",
            "system",
            "Dummy negotiation seeded for dashboard previews.",
            json.dumps(offer),
            0.58,
        ),
    )


def _pick_enterprise_id(conn: sqlite3.Connection) -> str:
    for enterprise_id in ("ent_demo", "ent_meridian_technologies"):
        row = conn.execute(
            "SELECT id FROM galileo_enterprises WHERE id = ? LIMIT 1",
            (enterprise_id,),
        ).fetchone()
        if row is not None:
            return enterprise_id

    conn.execute(
        """
        INSERT INTO galileo_enterprises (
            id, name, description, total_saved_hotels, total_saved_airlines,
            total_saved, yoy_change, hotel_contract_count, airline_contract_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "ent_demo",
            "Demo Enterprise",
            "Dummy enterprise created for route probing.",
            0.0,
            0.0,
            0.0,
            0.0,
            0,
            0,
        ),
    )
    return "ent_demo"


def seed_galileo_agent_new(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM galileo_agents WHERE id = ? LIMIT 1",
        ("new",),
    ).fetchone()
    if row is not None:
        return

    enterprise_id = _pick_enterprise_id(conn)
    company_id = "cmp_dummy_new"
    event_id = "evt_dummy_new"

    conn.execute(
        """
        INSERT OR IGNORE INTO galileo_companies (
            id, name, initials, description, phone, website, industry, badge
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            company_id,
            "Dummy Skyline Hotels",
            "DSH",
            "Dummy supplier for GET /api/galileo/agents/new.",
            "+1-312-555-0199",
            "https://dummy-skyline.example",
            "Hotel",
            "Demo",
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO galileo_locations (
            id, company_id, name, address, phone
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "loc_dummy_new",
            company_id,
            "Chicago Loop Demo",
            "200 W Madison St, Chicago, IL 60606",
            "+1-312-555-0199",
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO galileo_events (
            id, enterprise_id, name, location, start_date, end_date, attendees,
            service, status, requirements, budget_per_person
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            enterprise_id,
            "Dummy Event",
            "Chicago, IL",
            "2026-09-10",
            "2026-09-12",
            120,
            "Hotel",
            "Active",
            json.dumps({"notes": "Dummy event for route probing"}),
            245.0,
        ),
    )
    conn.execute(
        """
        INSERT INTO galileo_agents (
            id, enterprise_id, event_id, company_id, company_name, segment, type, status,
            lifecycle_status, outcome, ideal_price, ceiling_price, original_price, current_price,
            delta, potential_savings, savings_to_date, distance_to_goal, is_accepted
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "new",
            enterprise_id,
            event_id,
            company_id,
            "Dummy Skyline Hotels",
            "Business",
            "Hotel",
            "Negotiating",
            "ACTIVE",
            None,
            225.0,
            280.0,
            295.0,
            252.0,
            -14.58,
            70.0,
            43.0,
            27.0,
            0,
        ),
    )
    conn.execute(
        """
        INSERT INTO galileo_price_points (agent_id, label, price, type, round)
        VALUES
            (?, 'Opening', 295.0, 'offer', 1),
            (?, 'Counter', 268.0, 'negotiated', 2),
            (?, 'Current', 252.0, 'negotiated', 3)
        """,
        ("new", "new", "new"),
    )
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO galileo_activity_stream (
            id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
        )
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "act_new_1",
            "new",
            295.0,
            "Opening",
            "neutral",
            "Dummy supplier opened at market rate.",
            "neutral",
            now,
            0,
            "act_new_2",
            "new",
            252.0,
            "Current",
            "savings",
            "Dummy current rate ready for UI preview.",
            "positive",
            now,
            1,
        ),
    )
    conn.execute(
        """
        INSERT INTO galileo_messages (id, agent_id, message, sender, timestamp)
        VALUES
            (?, ?, ?, ?, ?),
            (?, ?, ?, ?, ?)
        """,
        (
            "msg_new_1",
            "new",
            "We are benchmarking rates for a Chicago event block.",
            "Galileo",
            now,
            "msg_new_2",
            "new",
            "We can hold 252.00 per night with breakfast included.",
            "Rep",
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO galileo_previous_negotiations (
            id, agent_id, contract_id, region, duration, final_rate, total_savings,
            status, start_date, end_date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "prev_new_1",
            "new",
            "CTR-NEW-001",
            "North America",
            "12 months",
            259.0,
            18000.0,
            "ARCHIVED",
            "2025-08-01",
            "2026-07-31",
        ),
    )


def seed_hotel_ord_quotes(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "quote_events") or not _table_exists(conn, "latest_quotes"):
        return

    negotiation_row = conn.execute(
        "SELECT 1 FROM negotiations WHERE id = ? LIMIT 1",
        ("hotel-ord",),
    ).fetchone()
    if negotiation_row is None:
        config = {
            "target_unit_price": 210.0,
            "max_unit_price": 245.0,
            "target_shipping_cost": 0.0,
            "max_shipping_cost": 0.0,
            "preferred_payment_terms": 30,
            "min_payment_terms": 14,
            "preferred_delivery_days": 7,
            "max_delivery_days": 14,
            "quantity": 12,
            "weight_price": 0.6,
            "weight_shipping": 0.0,
            "weight_payment_terms": 0.2,
            "weight_delivery": 0.2,
            "min_acceptable_utility": 0.55,
        }
        current_offer = {
            "unit_price": 219.0,
            "shipping_cost": 0.0,
            "payment_terms_days": 30,
            "delivery_days": 7,
            "notes": "Backing negotiation row for hotel quote dummy data",
        }
        conn.execute(
            """
            INSERT INTO negotiations (
                id, vendor_name, product_category, status, strategy, config,
                current_offer, research_brief, utility_score, round_number, max_rounds,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                "hotel-ord",
                "ORD Skyline Hotel",
                "hotel",
                "pending",
                "balanced",
                json.dumps(config),
                json.dumps(current_offer),
                json.dumps({"summary": "Dummy negotiation backing hotel-ord quote seed."}),
                0.74,
                2,
                10,
            ),
        )

    quote_row = conn.execute(
        "SELECT 1 FROM latest_quotes WHERE negotiation_id = ? LIMIT 1",
        ("hotel-ord",),
    ).fetchone()
    if quote_row is None:
        offer = {
            "nightly_rate": 219.0,
            "total_rate": 657.0,
            "fees": 49.0,
            "rate_type": "corporate",
            "inclusions": {
                "breakfast": True,
                "wifi": True,
                "parking": False,
            },
            "cancellation_policy": "24 hours",
        }
        facts = {
            "hotel_id": "hotel-ord",
            "hotel_name": "ORD Skyline Hotel",
            "location": "Chicago, IL",
            "source": "dummy_404_seed",
        }
        conn.execute(
            """
            INSERT INTO quote_events (
                negotiation_id, vendor_name, product_category, extracted_facts, offer, created_at
            )
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                "hotel-ord",
                "ORD Skyline Hotel",
                "hotel",
                json.dumps(facts),
                json.dumps(offer),
            ),
        )
        conn.execute(
            """
            INSERT INTO latest_quotes (
                negotiation_id, vendor_name, product_category, extracted_facts, offer, utility_score, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                "hotel-ord",
                "ORD Skyline Hotel",
                "hotel",
                json.dumps(facts),
                json.dumps(offer),
                0.74,
            ),
        )


def seed_hotel_market_rows(conn: sqlite3.Connection) -> None:
    if _table_exists(conn, "historic_pricing"):
        columns = _table_columns(conn, "historic_pricing")
        exists = conn.execute(
            "SELECT 1 FROM historic_pricing WHERE hotel = ? LIMIT 1",
            ("ORD Skyline Hotel",),
        ).fetchone()
        if exists is None:
            if {"month", "year"}.issubset(columns):
                conn.execute(
                    """
                    INSERT INTO historic_pricing (hotel, location, month, year, price_per_night)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("ORD Skyline Hotel", "Chicago, IL", 9, 2026, 249.0),
                )
            elif "date" in columns:
                conn.execute(
                    """
                    INSERT INTO historic_pricing (hotel, location, date, price_per_night)
                    VALUES (?, ?, ?, ?)
                    """,
                    ("ORD Skyline Hotel", "Chicago, IL", "September 2026", 249.0),
                )

    if _table_exists(conn, "past_negotiations"):
        columns = _table_columns(conn, "past_negotiations")
        exists = conn.execute(
            "SELECT 1 FROM past_negotiations WHERE hotel = ? LIMIT 1",
            ("ORD Skyline Hotel",),
        ).fetchone()
        if exists is None:
            if {"month", "year"}.issubset(columns):
                conn.execute(
                    """
                    INSERT INTO past_negotiations (
                        hotel, location, month, year, starting_price, negotiation_price, proposed_price
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("ORD Skyline Hotel", "Chicago, IL", 9, 2026, 269.0, 229.0, 219.0),
                )
            elif "date" in columns:
                conn.execute(
                    """
                    INSERT INTO past_negotiations (
                        hotel, location, date, starting_price, negotiation_price, proposed_price
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("ORD Skyline Hotel", "Chicago, IL", "2026-09-10", 269.0, 229.0, 219.0),
                )


def seed_hotel_ord_redis() -> str:
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
    except Exception:
        return "redis-unavailable"

    session_id = "dummy-session-hotel-ord"
    timestamp = datetime.now(UTC).timestamp()
    document = (
        "Hotel: hotel-ord. Outcome: rate_confirmed. Summary: Front desk quoted 219/night "
        "for a small corporate block and was willing to include breakfast after escalation."
    )
    metadata = {
        "hotel_id": "hotel-ord",
        "outcome": "rate_confirmed",
        "best_rate": "219.0",
        "session_id": session_id,
    }

    pipe = client.pipeline()
    pipe.hset(
        f"hotel_call_history:{session_id}",
        mapping={
            "document": document,
            "metadata": json.dumps(metadata),
        },
    )
    pipe.zadd(f"hotel_call_history:hotel:hotel-ord", {session_id: timestamp})
    pipe.execute()
    return "redis-seeded"


def main() -> None:
    conn = _connect_db()
    try:
        seed_galileo_agent_new(conn)
        seed_hotel_ord_quotes(conn)
        seed_hotel_market_rows(conn)
        conn.commit()
    finally:
        conn.close()

    redis_status = seed_hotel_ord_redis()

    print("Seeded dummy data for:")
    print("- GET /api/galileo/agents/new via galileo_agents.id = 'new'")
    print("- hotel-ord backing negotiation plus quote/history records in existing SQL tables")
    print(f"- hotel-ord Redis priors: {redis_status}")
    print("")
    print("Still requires route implementation, not just data:")
    print("- GET /api/hotels/hotel-ord")
    print("- GET /api/memory/priors/hotel-ord")
    print("- POST /api/email/sessions/")
    print("- POST /api/email/messages/")


if __name__ == "__main__":
    main()

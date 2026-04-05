from __future__ import annotations

from datetime import datetime, timedelta

import aiosqlite

from app.core.database import DB_PATH

ENTERPRISE_ID = "ent_meridian_technologies"


def _iso(base: datetime, minutes: int) -> str:
    return (base + timedelta(minutes=minutes)).replace(microsecond=0).isoformat() + "Z"


def _build_price_path(agent: dict[str, object]) -> list[dict[str, object]]:
    original = float(agent["original_price"])
    current = float(agent["current_price"])
    midpoint = round((original + current) / 2, 2)
    return [
        {"label": "Market Baseline", "price": original, "type": "current", "round": 0},
        {"label": "Supplier Offer", "price": round(original * 0.97, 2), "type": "offer", "round": 1},
        {"label": "Galileo Counter", "price": midpoint, "type": "negotiated", "round": 2},
        {"label": "Latest Position", "price": current, "type": "negotiated", "round": 3},
    ]


def _build_activity(agent: dict[str, object], started_at: datetime) -> list[dict[str, object]]:
    current = float(agent["current_price"])
    original = float(agent["original_price"])
    active = agent["status"] in {"Negotiating", "Reviewing"}
    return [
        {
            "id": f"act_{agent['id']}_1",
            "agent_id": agent["id"],
            "price": original,
            "badge": "Queued",
            "badge_type": "neutral",
            "detail": "Agent queued and sourcing market comps.",
            "detail_type": "neutral",
            "timestamp": _iso(started_at, 0),
            "active": 0,
        },
        {
            "id": f"act_{agent['id']}_2",
            "agent_id": agent["id"],
            "price": round(original * 0.97, 2),
            "badge": "Supplier Reply",
            "badge_type": "neutral",
            "detail": "Supplier shared first revised rate.",
            "detail_type": "neutral",
            "timestamp": _iso(started_at, 11),
            "active": 0,
        },
        {
            "id": f"act_{agent['id']}_3",
            "agent_id": agent["id"],
            "price": round((original + current) / 2, 2),
            "badge": "Counter",
            "badge_type": "neutral",
            "detail": "Galileo submitted a counter aligned to guardrails.",
            "detail_type": "positive",
            "timestamp": _iso(started_at, 19),
            "active": 0,
        },
        {
            "id": f"act_{agent['id']}_4",
            "agent_id": agent["id"],
            "price": current,
            "badge": "Current",
            "badge_type": "savings" if agent["is_accepted"] else "neutral",
            "detail": "Current offer captured for review.",
            "detail_type": "positive" if agent["is_accepted"] else "neutral",
            "timestamp": _iso(started_at, 28),
            "active": 1 if active else 0,
        },
    ]


def _build_messages(agent: dict[str, object], started_at: datetime) -> list[dict[str, object]]:
    current = float(agent["current_price"])
    ideal = float(agent["ideal_price"])
    return [
        {
            "id": f"msg_{agent['id']}_1",
            "agent_id": agent["id"],
            "message": "Thanks for joining. We are benchmarking rates for this event block.",
            "sender": "Galileo",
            "timestamp": _iso(started_at, 1),
        },
        {
            "id": f"msg_{agent['id']}_2",
            "agent_id": agent["id"],
            "message": f"We can start at {round(current * 1.08, 2):.2f} with standard terms.",
            "sender": "Rep",
            "timestamp": _iso(started_at, 8),
        },
        {
            "id": f"msg_{agent['id']}_3",
            "agent_id": agent["id"],
            "message": f"We need to land near {ideal:.2f} to meet policy targets.",
            "sender": "Galileo",
            "timestamp": _iso(started_at, 16),
        },
        {
            "id": f"msg_{agent['id']}_4",
            "agent_id": agent["id"],
            "message": f"Understood. We can hold {current:.2f} with concessions.",
            "sender": "Rep",
            "timestamp": _iso(started_at, 24),
        },
    ]


def _build_previous_negotiations(agent: dict[str, object], started_at: datetime) -> list[dict[str, object]]:
    if agent["status"] not in {"Completed", "Optimized", "Cancelled"}:
        return []
    current = float(agent["current_price"])
    return [
        {
            "id": f"prev_{agent['id']}_1",
            "agent_id": agent["id"],
            "contract_id": f"CTR-{str(agent['id'])[-4:].upper()}-24",
            "region": "North America" if "london" not in str(agent["event_id"]).lower() else "EMEA",
            "duration": "12 months",
            "final_rate": round(current * 1.04, 2),
            "total_savings": round(float(agent["potential_savings"]) * 0.82, 2),
            "status": "ACTIVE" if agent["is_accepted"] else "ARCHIVED",
            "start_date": "2024-02-01",
            "end_date": "2025-01-31",
        }
    ]


async def _has_seed_data(conn: aiosqlite.Connection) -> bool:
    cursor = await conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='galileo_enterprises' LIMIT 1"
    )
    table_exists = await cursor.fetchone()
    await cursor.close()
    if not table_exists:
        return False

    cursor = await conn.execute("SELECT 1 FROM galileo_enterprises WHERE id = ? LIMIT 1", (ENTERPRISE_ID,))
    enterprise_exists = await cursor.fetchone()
    await cursor.close()
    return enterprise_exists is not None


async def seed_galileo_data() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")

        if await _has_seed_data(conn):
            return

        enterprise = {
            "id": ENTERPRISE_ID,
            "name": "Meridian Technologies",
            "description": "Fortune 500 cloud and enterprise infrastructure provider.",
            "total_saved_hotels": 1_600_000.0,
            "total_saved_airlines": 800_000.0,
            "total_saved": 2_400_000.0,
            "yoy_change": 18.5,
            "hotel_contract_count": 12,
            "airline_contract_count": 6,
        }

        companies = [
            {
                "id": "cmp_lumina",
                "name": "Lumina Hospitality Group",
                "initials": "LHG",
                "description": "Global upscale hotel portfolio focused on convention markets.",
                "phone": "+1-312-555-0142",
                "website": "https://luminahospitality.example",
                "industry": "Hotel",
                "badge": "Strategic Partner",
            },
            {
                "id": "cmp_apex",
                "name": "Apex Hotels & Resorts",
                "initials": "AHR",
                "description": "Business-first hotel chain with premium meeting inventory.",
                "phone": "+1-646-555-0104",
                "website": "https://apexhotels.example",
                "industry": "Hotel",
                "badge": "Preferred Supplier",
            },
            {
                "id": "cmp_vanguard",
                "name": "Vanguard Suites",
                "initials": "VGS",
                "description": "Extended-stay and suite inventory for distributed teams.",
                "phone": "+1-720-555-0188",
                "website": "https://vanguardsuites.example",
                "industry": "Hotel",
                "badge": "Preferred Supplier",
            },
            {
                "id": "cmp_coastal",
                "name": "Coastal Inn Collection",
                "initials": "CIC",
                "description": "Regional hotel operator with strong shoulder-season pricing.",
                "phone": "+1-415-555-0139",
                "website": "https://coastalinn.example",
                "industry": "Hotel",
                "badge": "Standard",
            },
            {
                "id": "cmp_atlas",
                "name": "Atlas Airways",
                "initials": "ATA",
                "description": "Transcontinental carrier with robust corporate fare programs.",
                "phone": "+1-404-555-0129",
                "website": "https://atlasairways.example",
                "industry": "Airline",
                "badge": "Strategic Partner",
            },
            {
                "id": "cmp_pinnacle",
                "name": "Pinnacle Airlines",
                "initials": "PNA",
                "description": "Premium carrier serving major North American and EU hubs.",
                "phone": "+1-206-555-0151",
                "website": "https://pinnacleair.example",
                "industry": "Airline",
                "badge": "Preferred Supplier",
            },
        ]

        locations = [
            {"id": "loc_lumina_chi", "company_id": "cmp_lumina", "name": "Chicago River North", "address": "230 W Kinzie St, Chicago, IL 60654", "phone": "+1-312-555-0161"},
            {"id": "loc_lumina_aus", "company_id": "cmp_lumina", "name": "Austin Downtown", "address": "415 Colorado St, Austin, TX 78701", "phone": "+1-512-555-0198"},
            {"id": "loc_lumina_lhr", "company_id": "cmp_lumina", "name": "London Canary Wharf", "address": "31 Marsh Wall, London E14 9TP, UK", "phone": "+44-20-5550-1120"},
            {"id": "loc_apex_sfo", "company_id": "cmp_apex", "name": "San Francisco Market", "address": "88 Howard St, San Francisco, CA 94105", "phone": "+1-415-555-0177"},
            {"id": "loc_apex_den", "company_id": "cmp_apex", "name": "Denver Union Station", "address": "1701 Wynkoop St, Denver, CO 80202", "phone": "+1-303-555-0112"},
            {"id": "loc_apex_london", "company_id": "cmp_apex", "name": "London City Hub", "address": "22 Bishopsgate, London EC2N 4BQ, UK", "phone": "+44-20-5550-1144"},
            {"id": "loc_vanguard_den", "company_id": "cmp_vanguard", "name": "Denver Tech Center", "address": "7800 E Tufts Ave, Denver, CO 80237", "phone": "+1-720-555-0147"},
            {"id": "loc_vanguard_aus", "company_id": "cmp_vanguard", "name": "Austin North", "address": "11620 N Interstate 35, Austin, TX 78753", "phone": "+1-512-555-0158"},
            {"id": "loc_vanguard_sfo", "company_id": "cmp_vanguard", "name": "South San Francisco", "address": "121 S Airport Blvd, South San Francisco, CA 94080", "phone": "+1-650-555-0133"},
            {"id": "loc_coastal_sf", "company_id": "cmp_coastal", "name": "Fisherman's Wharf", "address": "2100 Mason St, San Francisco, CA 94133", "phone": "+1-415-555-0183"},
            {"id": "loc_coastal_chi", "company_id": "cmp_coastal", "name": "Magnificent Mile", "address": "500 N Michigan Ave, Chicago, IL 60611", "phone": "+1-312-555-0116"},
            {"id": "loc_coastal_den", "company_id": "cmp_coastal", "name": "Downtown Denver", "address": "1450 Glenarm Pl, Denver, CO 80202", "phone": "+1-303-555-0199"},
            {"id": "loc_atlas_ord", "company_id": "cmp_atlas", "name": "Chicago O'Hare Hub", "address": "10000 W O'Hare Ave, Chicago, IL 60666", "phone": "+1-773-555-0169"},
            {"id": "loc_atlas_lhr", "company_id": "cmp_atlas", "name": "London Heathrow Desk", "address": "Heathrow Terminal 5, Hounslow TW6 2GA, UK", "phone": "+44-20-5550-1109"},
            {"id": "loc_pinnacle_sfo", "company_id": "cmp_pinnacle", "name": "San Francisco International", "address": "SFO International Terminal, San Francisco, CA 94128", "phone": "+1-650-555-0182"},
            {"id": "loc_pinnacle_den", "company_id": "cmp_pinnacle", "name": "Denver International", "address": "8500 Pena Blvd, Denver, CO 80249", "phone": "+1-303-555-0132"},
        ]

        events = [
            {
                "id": "evt_q4_kickoff_chicago",
                "enterprise_id": ENTERPRISE_ID,
                "name": "Q4 Sales Kickoff - Chicago",
                "location": "Chicago, IL",
                "start_date": "2026-10-07",
                "end_date": "2026-10-10",
                "attendees": 260,
                "service": "Hotel",
                "status": "Active",
                "requirements": "Downtown conference hotel, direct flight inventory, breakfast included.",
                "budget_per_person": 620.0,
            },
            {
                "id": "evt_emea_summit_london",
                "enterprise_id": ENTERPRISE_ID,
                "name": "EMEA Leadership Summit - London",
                "location": "London, UK",
                "start_date": "2026-09-15",
                "end_date": "2026-09-18",
                "attendees": 140,
                "service": "Hotel",
                "status": "Active",
                "requirements": "Canary Wharf proximity, executive floors, AV-enabled rooms.",
                "budget_per_person": 540.0,
            },
            {
                "id": "evt_retreat_austin",
                "enterprise_id": ENTERPRISE_ID,
                "name": "Annual Company Retreat - Austin",
                "location": "Austin, TX",
                "start_date": "2025-11-05",
                "end_date": "2025-11-09",
                "attendees": 320,
                "service": "Hotel",
                "status": "Completed",
                "requirements": "Resort venue with breakout rooms and airport transfer package.",
                "budget_per_person": 710.0,
            },
            {
                "id": "evt_offsite_denver",
                "enterprise_id": ENTERPRISE_ID,
                "name": "Engineering Offsite - Denver",
                "location": "Denver, CO",
                "start_date": "2025-08-11",
                "end_date": "2025-08-14",
                "attendees": 110,
                "service": "Hotel",
                "status": "Completed",
                "requirements": "Suite inventory with workshop spaces and late checkout.",
                "budget_per_person": 430.0,
            },
            {
                "id": "evt_conf_san_francisco",
                "enterprise_id": ENTERPRISE_ID,
                "name": "Customer Conference - San Francisco",
                "location": "San Francisco, CA",
                "start_date": "2025-05-20",
                "end_date": "2025-05-24",
                "attendees": 480,
                "service": "Hotel",
                "status": "Completed",
                "requirements": "Moscone-adjacent hotel blocks and preferred transcon fares.",
                "budget_per_person": 845.0,
            },
        ]

        agents = [
            {"id": "agt_q4_lumina", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_q4_kickoff_chicago", "company_id": "cmp_lumina", "company_name": "Lumina Hospitality Group", "segment": "Upper Upscale", "type": "Hotel", "status": "Negotiating", "lifecycle_status": "ACTIVE", "outcome": None, "ideal_price": 210.0, "ceiling_price": 255.0, "original_price": 268.0, "current_price": 233.0, "delta": -13.1, "potential_savings": 183400.0, "savings_to_date": 116100.0, "distance_to_goal": 23.0, "is_accepted": 0},
            {"id": "agt_q4_apex", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_q4_kickoff_chicago", "company_id": "cmp_apex", "company_name": "Apex Hotels & Resorts", "segment": "Luxury", "type": "Hotel", "status": "Reviewing", "lifecycle_status": "WRAPPING_UP", "outcome": None, "ideal_price": 215.0, "ceiling_price": 260.0, "original_price": 272.0, "current_price": 238.0, "delta": -12.5, "potential_savings": 175200.0, "savings_to_date": 101000.0, "distance_to_goal": 23.0, "is_accepted": 0},
            {"id": "agt_q4_atlas", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_q4_kickoff_chicago", "company_id": "cmp_atlas", "company_name": "Atlas Airways", "segment": "Domestic + EMEA", "type": "Airline", "status": "Negotiating", "lifecycle_status": "ACTIVE", "outcome": None, "ideal_price": 355.0, "ceiling_price": 470.0, "original_price": 505.0, "current_price": 429.0, "delta": -15.0, "potential_savings": 132700.0, "savings_to_date": 94000.0, "distance_to_goal": 74.0, "is_accepted": 0},
            {"id": "agt_q4_pinnacle", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_q4_kickoff_chicago", "company_id": "cmp_pinnacle", "company_name": "Pinnacle Airlines", "segment": "Domestic", "type": "Airline", "status": "Queued", "lifecycle_status": "INITIALIZING", "outcome": None, "ideal_price": 365.0, "ceiling_price": 465.0, "original_price": 490.0, "current_price": 468.0, "delta": -4.5, "potential_savings": 97600.0, "savings_to_date": 0.0, "distance_to_goal": 103.0, "is_accepted": 0},
            {"id": "agt_emea_vanguard", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_emea_summit_london", "company_id": "cmp_vanguard", "company_name": "Vanguard Suites", "segment": "Extended Stay", "type": "Hotel", "status": "Negotiating", "lifecycle_status": "ACTIVE", "outcome": None, "ideal_price": 235.0, "ceiling_price": 295.0, "original_price": 304.0, "current_price": 268.0, "delta": -11.8, "potential_savings": 74200.0, "savings_to_date": 39600.0, "distance_to_goal": 33.0, "is_accepted": 0},
            {"id": "agt_emea_coastal", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_emea_summit_london", "company_id": "cmp_coastal", "company_name": "Coastal Inn Collection", "segment": "Business", "type": "Hotel", "status": "Queued", "lifecycle_status": "INITIALIZING", "outcome": None, "ideal_price": 240.0, "ceiling_price": 300.0, "original_price": 299.0, "current_price": 289.0, "delta": -3.3, "potential_savings": 51100.0, "savings_to_date": 0.0, "distance_to_goal": 49.0, "is_accepted": 0},
            {"id": "agt_emea_lumina", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_emea_summit_london", "company_id": "cmp_lumina", "company_name": "Lumina Hospitality Group", "segment": "Upper Upscale", "type": "Hotel", "status": "Reviewing", "lifecycle_status": "WRAPPING_UP", "outcome": None, "ideal_price": 232.0, "ceiling_price": 292.0, "original_price": 298.0, "current_price": 257.0, "delta": -13.8, "potential_savings": 81500.0, "savings_to_date": 50200.0, "distance_to_goal": 25.0, "is_accepted": 0},
            {"id": "agt_retreat_lumina", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_retreat_austin", "company_id": "cmp_lumina", "company_name": "Lumina Hospitality Group", "segment": "Resort", "type": "Hotel", "status": "Completed", "lifecycle_status": "COMPLETED", "outcome": "RATE_CONFIRMED", "ideal_price": 228.0, "ceiling_price": 280.0, "original_price": 292.0, "current_price": 236.0, "delta": -19.2, "potential_savings": 287000.0, "savings_to_date": 287000.0, "distance_to_goal": 8.0, "is_accepted": 1},
            {"id": "agt_retreat_apex", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_retreat_austin", "company_id": "cmp_apex", "company_name": "Apex Hotels & Resorts", "segment": "Resort", "type": "Hotel", "status": "Cancelled", "lifecycle_status": "COMPLETED", "outcome": "FAILED", "ideal_price": 235.0, "ceiling_price": 290.0, "original_price": 301.0, "current_price": 274.0, "delta": -9.0, "potential_savings": 149300.0, "savings_to_date": 0.0, "distance_to_goal": 39.0, "is_accepted": 0},
            {"id": "agt_retreat_atlas", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_retreat_austin", "company_id": "cmp_atlas", "company_name": "Atlas Airways", "segment": "Domestic", "type": "Airline", "status": "Completed", "lifecycle_status": "COMPLETED", "outcome": "RATE_CONFIRMED", "ideal_price": 320.0, "ceiling_price": 430.0, "original_price": 462.0, "current_price": 338.0, "delta": -26.8, "potential_savings": 196400.0, "savings_to_date": 196400.0, "distance_to_goal": 18.0, "is_accepted": 1},
            {"id": "agt_retreat_pinnacle", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_retreat_austin", "company_id": "cmp_pinnacle", "company_name": "Pinnacle Airlines", "segment": "Domestic", "type": "Airline", "status": "Cancelled", "lifecycle_status": "FAILED", "outcome": "FAILED", "ideal_price": 328.0, "ceiling_price": 435.0, "original_price": 455.0, "current_price": 366.0, "delta": -19.6, "potential_savings": 163900.0, "savings_to_date": 0.0, "distance_to_goal": 38.0, "is_accepted": 0},
            {"id": "agt_offsite_vanguard", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_offsite_denver", "company_id": "cmp_vanguard", "company_name": "Vanguard Suites", "segment": "Extended Stay", "type": "Hotel", "status": "Optimized", "lifecycle_status": "COMPLETED", "outcome": "RATE_CONFIRMED", "ideal_price": 182.0, "ceiling_price": 235.0, "original_price": 244.0, "current_price": 189.0, "delta": -22.5, "potential_savings": 102300.0, "savings_to_date": 102300.0, "distance_to_goal": 7.0, "is_accepted": 1},
            {"id": "agt_offsite_coastal", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_offsite_denver", "company_id": "cmp_coastal", "company_name": "Coastal Inn Collection", "segment": "Business", "type": "Hotel", "status": "Cancelled", "lifecycle_status": "COMPLETED", "outcome": "ESCALATED_TO_HUMAN", "ideal_price": 188.0, "ceiling_price": 236.0, "original_price": 248.0, "current_price": 214.0, "delta": -13.7, "potential_savings": 70600.0, "savings_to_date": 0.0, "distance_to_goal": 26.0, "is_accepted": 0},
            {"id": "agt_conf_apex", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_conf_san_francisco", "company_id": "cmp_apex", "company_name": "Apex Hotels & Resorts", "segment": "Convention", "type": "Hotel", "status": "Completed", "lifecycle_status": "COMPLETED", "outcome": "RATE_CONFIRMED", "ideal_price": 265.0, "ceiling_price": 345.0, "original_price": 358.0, "current_price": 278.0, "delta": -22.3, "potential_savings": 344500.0, "savings_to_date": 344500.0, "distance_to_goal": 13.0, "is_accepted": 1},
            {"id": "agt_conf_pinnacle", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_conf_san_francisco", "company_id": "cmp_pinnacle", "company_name": "Pinnacle Airlines", "segment": "Transcon", "type": "Airline", "status": "Completed", "lifecycle_status": "COMPLETED", "outcome": "RATE_CONFIRMED", "ideal_price": 395.0, "ceiling_price": 520.0, "original_price": 552.0, "current_price": 418.0, "delta": -24.3, "potential_savings": 298200.0, "savings_to_date": 298200.0, "distance_to_goal": 23.0, "is_accepted": 1},
            {"id": "agt_conf_atlas", "enterprise_id": ENTERPRISE_ID, "event_id": "evt_conf_san_francisco", "company_id": "cmp_atlas", "company_name": "Atlas Airways", "segment": "Transcon", "type": "Airline", "status": "Cancelled", "lifecycle_status": "COMPLETED", "outcome": "FAILED", "ideal_price": 402.0, "ceiling_price": 528.0, "original_price": 548.0, "current_price": 437.0, "delta": -20.3, "potential_savings": 252600.0, "savings_to_date": 0.0, "distance_to_goal": 35.0, "is_accepted": 0},
        ]

        await conn.execute(
            """
            INSERT INTO galileo_enterprises (
                id, name, description, total_saved_hotels, total_saved_airlines,
                total_saved, yoy_change, hotel_contract_count, airline_contract_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                enterprise["id"],
                enterprise["name"],
                enterprise["description"],
                enterprise["total_saved_hotels"],
                enterprise["total_saved_airlines"],
                enterprise["total_saved"],
                enterprise["yoy_change"],
                enterprise["hotel_contract_count"],
                enterprise["airline_contract_count"],
            ),
        )

        await conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_companies (
                id, name, initials, description, phone, website, industry, badge
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c["id"],
                    c["name"],
                    c["initials"],
                    c["description"],
                    c["phone"],
                    c["website"],
                    c["industry"],
                    c["badge"],
                )
                for c in companies
            ],
        )

        await conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_locations (
                id, company_id, name, address, phone
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [(l["id"], l["company_id"], l["name"], l["address"], l["phone"]) for l in locations],
        )

        await conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_events (
                id, enterprise_id, name, location, start_date, end_date,
                attendees, service, status, requirements, budget_per_person
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    e["id"],
                    e["enterprise_id"],
                    e["name"],
                    e["location"],
                    e["start_date"],
                    e["end_date"],
                    e["attendees"],
                    e["service"],
                    e["status"],
                    e["requirements"],
                    e["budget_per_person"],
                )
                for e in events
            ],
        )

        await conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_agents (
                id, enterprise_id, event_id, company_name, company_id, status,
                outcome, ideal_price, ceiling_price, market_price, current_price, is_accepted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    a["id"],
                    a["enterprise_id"],
                    a["event_id"],
                    a["company_name"],
                    a["company_id"],
                    a["status"],
                    a["outcome"],
                    a["ideal_price"],
                    a["ceiling_price"],
                    a["original_price"],
                    a["current_price"],
                    a["is_accepted"],
                )
                for a in agents
            ],
        )

        price_points: list[tuple[object, ...]] = []
        activities: list[tuple[object, ...]] = []
        messages: list[tuple[object, ...]] = []
        previous_negotiations: list[tuple[object, ...]] = []
        base = datetime(2026, 3, 1, 14, 0, 0)

        for index, agent in enumerate(agents):
            start = base + timedelta(hours=index * 3)
            for point in _build_price_path(agent):
                price_points.append((agent["id"], point["label"], point["price"], point["type"], point["round"]))
            for item in _build_activity(agent, start):
                activities.append(
                    (
                        item["id"],
                        item["agent_id"],
                        item["price"],
                        item["badge"],
                        item["badge_type"],
                        item["detail"],
                        item["detail_type"],
                        item["timestamp"],
                        item["active"],
                    )
                )
            for message in _build_messages(agent, start):
                messages.append(
                    (
                        message["id"],
                        message["agent_id"],
                        message["message"],
                        message["sender"],
                        message["timestamp"],
                    )
                )
            for previous in _build_previous_negotiations(agent, start):
                previous_negotiations.append(
                    (
                        previous["id"],
                        previous["agent_id"],
                        previous["contract_id"],
                        previous["region"],
                        previous["duration"],
                        previous["final_rate"],
                        previous["total_savings"],
                        previous["status"],
                        previous["start_date"],
                        previous["end_date"],
                    )
                )

        await conn.executemany(
            """
            INSERT INTO galileo_price_points (agent_id, label, price, type, round)
            VALUES (?, ?, ?, ?, ?)
            """,
            price_points,
        )

        await conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            activities,
        )

        await conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_messages (
                id, agent_id, message, sender, timestamp
            ) VALUES (?, ?, ?, ?, ?)
            """,
            messages,
        )

        await conn.executemany(
            """
            INSERT OR IGNORE INTO galileo_previous_negotiations (
                id, agent_id, contract_id, region, duration, final_rate, total_savings,
                status, start_date, end_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            previous_negotiations,
        )

        await conn.commit()

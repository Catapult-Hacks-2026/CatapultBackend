from __future__ import annotations

import json
import random
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from app.core.database import DB_PATH


GALILEO_NEGOTIATING_STATUS = "Negotiating"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _coerce_json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _parse_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _get_value(payload: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return default


async def _connect() -> aiosqlite.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _serialize_enterprise(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "totalSavedHotels": float(row["total_saved_hotels"] or 0),
        "totalSavedAirlines": float(row["total_saved_airlines"] or 0),
        "totalSaved": float(row["total_saved"] or 0),
        "yoyChange": float(row["yoy_change"] or 0),
        "hotelContractCount": int(row["hotel_contract_count"] or 0),
        "airlineContractCount": int(row["airline_contract_count"] or 0),
    }


def _serialize_company(
    row: aiosqlite.Row | None,
    *,
    locations: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if row is None:
        return None
    payload: dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "initials": row["initials"],
        "description": row["description"],
        "phone": row["phone"],
        "website": row["website"],
        "industry": row["industry"],
        "badge": row["badge"],
    }
    if locations is not None:
        payload["locations"] = locations
    return payload


def _serialize_location(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "companyId": row["company_id"],
        "name": row["name"],
        "address": row["address"],
        "phone": row["phone"],
    }


def _serialize_price_point(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "label": row["label"],
        "price": float(row["price"] or 0),
        "type": row["type"],
        "round": int(row["round"] or 0),
    }


def _serialize_activity_item(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "price": float(row["price"] or 0),
        "badge": row["badge"],
        "badgeType": row["badge_type"],
        "detail": row["detail"],
        "detailType": row["detail_type"],
        "timestamp": row["timestamp"],
        "active": bool(row["active"]),
    }


def _serialize_message(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "message": row["message"],
        "sender": row["sender"],
        "timestamp": row["timestamp"],
    }


def _serialize_previous_negotiation(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "contractId": row["contract_id"],
        "region": row["region"],
        "duration": row["duration"],
        "finalRate": float(row["final_rate"] or 0),
        "totalSavings": float(row["total_savings"] or 0),
        "status": row["status"],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
    }


def _serialize_agent(
    row: aiosqlite.Row,
    *,
    price_path: list[dict[str, Any]] | None = None,
    activity_stream: list[dict[str, Any]] | None = None,
    transcript: list[dict[str, Any]] | None = None,
    previous_negotiations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": row["id"],
        "enterpriseId": row["enterprise_id"],
        "eventId": row["event_id"],
        "companyId": row["company_id"],
        "companyName": row["company_name"],
        "status": row["status"],
        "outcome": row["outcome"],
        "idealPrice": float(row["ideal_price"] or 0),
        "ceilingPrice": float(row["ceiling_price"] or 0),
        "marketPrice": float(row["market_price"] or 0),
        "currentPrice": float(row["current_price"] or 0),
        "isAccepted": bool(row["is_accepted"]),
    }
    payload["pricePath"] = price_path or []
    payload["activityStream"] = activity_stream or []
    payload["transcript"] = transcript or []
    payload["previousNegotiations"] = previous_negotiations or []
    return payload


def _serialize_event(
    row: aiosqlite.Row,
    *,
    agents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": row["id"],
        "enterpriseId": row["enterprise_id"],
        "name": row["name"],
        "location": row["location"],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "attendees": int(row["attendees"] or 0),
        "service": row["service"],
        "status": row["status"],
        "agents": agents or [],
        "requirements": row["requirements"],
        "budgetPerPerson": float(row["budget_per_person"]) if row["budget_per_person"] is not None else None,
    }


async def append_agent_message(agent_id: str, message: str, sender: str) -> None:
    content = (message or "").strip()
    if not content:
        return

    conn = await _connect()
    try:
        await conn.execute(
            """
            INSERT INTO galileo_messages (id, agent_id, message, sender, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid4()), agent_id, content, sender, _now_iso()),
        )
        await conn.commit()
    finally:
        await conn.close()


async def mark_agent_dialing(agent_id: str, call_sid: str) -> None:
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        row = await _fetchone(
            conn,
            "SELECT current_price FROM galileo_agents WHERE id = ?",
            (agent_id,),
        )
        if row is None:
            await conn.rollback()
            return

        await conn.execute(
            """
            UPDATE galileo_agents
            SET status = ?
            WHERE id = ?
            """,
            (GALILEO_NEGOTIATING_STATUS, agent_id),
        )
        await conn.execute("UPDATE galileo_activity_stream SET active = 0 WHERE agent_id = ?", (agent_id,))
        await conn.execute(
            """
            INSERT INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                str(uuid4()),
                agent_id,
                float(row["current_price"] or 0),
                "Dialing",
                "neutral",
                f"Live call started. Call SID: {call_sid}",
                "neutral",
                _now_iso(),
            ),
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def record_agent_quote(
    agent_id: str,
    nightly_rate: float,
    rate_type: str = "",
) -> None:
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        row = await _fetchone(
            conn,
            """
            SELECT current_price
            FROM galileo_agents
            WHERE id = ?
            """,
            (agent_id,),
        )
        if row is None:
            await conn.rollback()
            return

        cursor = await conn.execute(
            "SELECT COALESCE(MAX(round), 0) FROM galileo_price_points WHERE agent_id = ?",
            (agent_id,),
        )
        max_round_row = await cursor.fetchone()
        await cursor.close()
        round_number = int(max_round_row[0] or 0) + 1
        point_type = "offer" if round_number <= 2 else "negotiated"
        label_suffix = f" ({rate_type})" if rate_type else ""

        await conn.execute(
            """
            UPDATE galileo_agents
            SET status = ?, current_price = ?
            WHERE id = ?
            """,
            (GALILEO_NEGOTIATING_STATUS, nightly_rate, agent_id),
        )
        await conn.execute(
            """
            INSERT INTO galileo_price_points (agent_id, label, price, type, round)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                f"Live Quote{label_suffix}",
                nightly_rate,
                point_type,
                round_number,
            ),
        )
        await conn.execute("UPDATE galileo_activity_stream SET active = 0 WHERE agent_id = ?", (agent_id,))
        await conn.execute(
            """
            INSERT INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                str(uuid4()),
                agent_id,
                nightly_rate,
                "Quote Received",
                "neutral",
                f"Hotel quoted ${nightly_rate:.2f}/night{label_suffix}.",
                "positive",
                _now_iso(),
            ),
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


def _final_status_for_outcome(outcome: str | None) -> tuple[str, str | None, str, str]:
    normalized = (outcome or "").upper()
    if normalized == "CALLBACK_REQUESTED":
        return ("Reviewing", normalized, "Follow-up", "Hotel requested callback or offline follow-up.")
    if normalized == "ESCALATED_TO_HUMAN":
        return ("Reviewing", normalized, "Escalated", "Negotiation requires human follow-up.")
    if normalized == "NO_AVAILABILITY":
        return ("Completed", normalized, "No Availability", "Hotel reported no availability for the requested stay.")
    if normalized == "TIMED_OUT":
        return ("Completed", normalized, "Timed Out", "Live negotiation timed out before a final agreement.")
    if normalized == "FAILED":
        return ("Completed", normalized, "Failed", "Live negotiation completed without a usable agreement.")
    return ("Completed", normalized or None, "Completed", "Live negotiation completed.")


async def finalize_agent_run(
    agent_id: str,
    outcome: str | None,
    best_rate: float | None = None,
) -> None:
    conn = await _connect()
    try:
        row = await _fetchone(
            conn,
            """
            SELECT enterprise_id, event_id, current_price
            FROM galileo_agents
            WHERE id = ?
            """,
            (agent_id,),
        )
    finally:
        await conn.close()

    if row is None:
        return

    normalized = (outcome or "").upper()
    if normalized == "RATE_CONFIRMED":
        await accept_offer(row["event_id"], agent_id, row["enterprise_id"])
        final_price = best_rate if best_rate is not None else float(row["current_price"] or 0)
        conn = await _connect()
        try:
            await conn.execute("BEGIN IMMEDIATE")
            await conn.execute("UPDATE galileo_activity_stream SET active = 0 WHERE agent_id = ?", (agent_id,))
            await conn.execute(
                """
                INSERT INTO galileo_activity_stream (
                    id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    str(uuid4()),
                    agent_id,
                    final_price,
                    "Accepted",
                    "savings",
                    f"Negotiation closed successfully at ${final_price:.2f}/night.",
                    "positive",
                    _now_iso(),
                ),
            )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()
        return

    status, stored_outcome, badge, detail = _final_status_for_outcome(outcome)
    final_price = best_rate if best_rate is not None else float(row["current_price"] or 0)
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute(
            """
            UPDATE galileo_agents
            SET status = ?, outcome = ?, current_price = ?, is_accepted = 0
            WHERE id = ?
            """,
            (status, stored_outcome, final_price, agent_id),
        )
        await conn.execute("UPDATE galileo_activity_stream SET active = 0 WHERE agent_id = ?", (agent_id,))
        await conn.execute(
            """
            INSERT INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                agent_id,
                final_price,
                badge,
                "neutral" if normalized in {"CALLBACK_REQUESTED", "ESCALATED_TO_HUMAN"} else "error",
                detail,
                "neutral" if normalized in {"CALLBACK_REQUESTED", "ESCALATED_TO_HUMAN"} else "negative",
                _now_iso(),
                1,
            ),
        )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def _fetchone(conn: aiosqlite.Connection, query: str, params: tuple[Any, ...] = ()) -> aiosqlite.Row | None:
    cursor = await conn.execute(query, params)
    row = await cursor.fetchone()
    await cursor.close()
    return row


async def _fetchall(conn: aiosqlite.Connection, query: str, params: tuple[Any, ...] = ()) -> list[aiosqlite.Row]:
    cursor = await conn.execute(query, params)
    rows = await cursor.fetchall()
    await cursor.close()
    return rows


async def _load_agent_children(conn: aiosqlite.Connection, agent_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    price_rows = await _fetchall(
        conn,
        """
        SELECT label, price, type, round
        FROM galileo_price_points
        WHERE agent_id = ?
        ORDER BY round ASC, id ASC
        """,
        (agent_id,),
    )
    activity_rows = await _fetchall(
        conn,
        """
        SELECT id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
        FROM galileo_activity_stream
        WHERE agent_id = ?
        ORDER BY timestamp ASC, id ASC
        """,
        (agent_id,),
    )
    message_rows = await _fetchall(
        conn,
        """
        SELECT id, agent_id, message, sender, timestamp
        FROM galileo_messages
        WHERE agent_id = ?
        ORDER BY timestamp ASC, id ASC
        """,
        (agent_id,),
    )
    previous_rows = await _fetchall(
        conn,
        """
        SELECT id, contract_id, region, duration, final_rate, total_savings, status, start_date, end_date
        FROM galileo_previous_negotiations
        WHERE agent_id = ?
        ORDER BY start_date DESC, id DESC
        """,
        (agent_id,),
    )
    return (
        [_serialize_price_point(row) for row in price_rows],
        [_serialize_activity_item(row) for row in activity_rows],
        [_serialize_message(row) for row in message_rows],
        [_serialize_previous_negotiation(row) for row in previous_rows],
    )


def _price_range_for_service(service_type: str) -> tuple[float, float]:
    normalized = service_type.strip().lower()
    if normalized == "airline":
        return (220.0, 820.0)
    return (150.0, 360.0)


def _company_matches_service(company_row: aiosqlite.Row, service_type: str) -> bool:
    normalized = service_type.strip().lower()
    searchable = " ".join(
        [
            str(company_row["industry"] or ""),
            str(company_row["name"] or ""),
            str(company_row["description"] or ""),
        ]
    ).lower()
    return "hotel" in searchable or "hospitality" in searchable or "resort" in searchable


def _required_service_types(service: str) -> list[str]:
    return ["Hotel"]


def _is_event_complete(event_service: str, accepted_types: set[str]) -> bool:
    return "hotel" in {value.lower() for value in accepted_types}


async def init_galileo_db() -> None:
    conn = await _connect()
    try:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS galileo_enterprises (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                total_saved_hotels REAL DEFAULT 0,
                total_saved_airlines REAL DEFAULT 0,
                total_saved REAL DEFAULT 0,
                yoy_change REAL DEFAULT 0,
                hotel_contract_count INTEGER DEFAULT 0,
                airline_contract_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS galileo_companies (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                initials TEXT,
                description TEXT,
                phone TEXT,
                website TEXT,
                industry TEXT,
                badge TEXT
            );

            CREATE TABLE IF NOT EXISTS galileo_locations (
                id TEXT PRIMARY KEY,
                company_id TEXT NOT NULL REFERENCES galileo_companies(id),
                name TEXT NOT NULL,
                address TEXT,
                phone TEXT
            );

            CREATE TABLE IF NOT EXISTS galileo_events (
                id TEXT PRIMARY KEY,
                enterprise_id TEXT NOT NULL REFERENCES galileo_enterprises(id),
                name TEXT NOT NULL,
                location TEXT,
                start_date TEXT,
                end_date TEXT,
                attendees INTEGER,
                service TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Active',
                requirements TEXT,
                budget_per_person REAL
            );

            CREATE TABLE IF NOT EXISTS galileo_agents (
                id TEXT PRIMARY KEY,
                enterprise_id TEXT NOT NULL,
                event_id TEXT NOT NULL REFERENCES galileo_events(id),
                company_name TEXT,
                company_id TEXT NOT NULL REFERENCES galileo_companies(id),
                status TEXT NOT NULL DEFAULT 'Queued',
                outcome TEXT,
                ideal_price REAL,
                ceiling_price REAL,
                market_price REAL,
                current_price REAL,
                is_accepted INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS galileo_price_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
                label TEXT,
                price REAL,
                type TEXT,
                round INTEGER
            );

            CREATE TABLE IF NOT EXISTS galileo_activity_stream (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
                price REAL,
                badge TEXT,
                badge_type TEXT,
                detail TEXT,
                detail_type TEXT,
                timestamp TEXT,
                active INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS galileo_messages (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
                message TEXT,
                sender TEXT,
                timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS galileo_previous_negotiations (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL REFERENCES galileo_agents(id),
                contract_id TEXT,
                region TEXT,
                duration TEXT,
                final_rate REAL,
                total_savings REAL,
                status TEXT,
                start_date TEXT,
                end_date TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_galileo_events_enterprise_status
            ON galileo_events (enterprise_id, status);

            CREATE INDEX IF NOT EXISTS idx_galileo_agents_enterprise_event
            ON galileo_agents (enterprise_id, event_id);

            CREATE INDEX IF NOT EXISTS idx_galileo_agents_company_status
            ON galileo_agents (company_id, status, is_accepted);

            CREATE INDEX IF NOT EXISTS idx_galileo_agents_event
            ON galileo_agents (event_id);

            CREATE INDEX IF NOT EXISTS idx_galileo_locations_company
            ON galileo_locations (company_id);

            CREATE INDEX IF NOT EXISTS idx_galileo_price_points_agent
            ON galileo_price_points (agent_id, round);

            CREATE INDEX IF NOT EXISTS idx_galileo_activity_agent_time
            ON galileo_activity_stream (agent_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_galileo_messages_agent_time
            ON galileo_messages (agent_id, timestamp);

            CREATE INDEX IF NOT EXISTS idx_galileo_prev_negotiations_agent
            ON galileo_previous_negotiations (agent_id, start_date);
            """
        )
        # Normalize all service values to 'Hotel'
        await conn.execute(
            "UPDATE galileo_events SET service = 'Hotel' WHERE service != 'Hotel'"
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_enterprise(enterprise_id: str) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        row = await _fetchone(
            conn,
            """
            SELECT id, name, description, total_saved_hotels, total_saved_airlines, total_saved, yoy_change,
                   hotel_contract_count, airline_contract_count
            FROM galileo_enterprises
            WHERE id = ?
            """,
            (enterprise_id,),
        )
        return _serialize_enterprise(row)
    finally:
        await conn.close()


async def get_agents(
    enterprise_id: str,
    status: str | None = None,
    event_id: str | None = None,
    limit: int | None = 50,
) -> list[dict[str, Any]]:
    limit_value = max(1, min(limit or 50, 500))
    query = """
        SELECT id, enterprise_id, event_id, company_name, company_id, status,
               outcome, ideal_price, ceiling_price, market_price, current_price, is_accepted
        FROM galileo_agents
        WHERE enterprise_id = ?
    """
    params: list[Any] = [enterprise_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    if event_id:
        query += " AND event_id = ?"
        params.append(event_id)
    query += " ORDER BY rowid DESC LIMIT ?"
    params.append(limit_value)

    conn = await _connect()
    try:
        rows = await _fetchall(conn, query, tuple(params))
        return [_serialize_agent(row) for row in rows]
    finally:
        await conn.close()


async def get_agent(agent_id: str) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        row = await _fetchone(
            conn,
            """
            SELECT id, enterprise_id, event_id, company_name, company_id, status,
                   outcome, ideal_price, ceiling_price, market_price, current_price, is_accepted
            FROM galileo_agents
            WHERE id = ?
            """,
            (agent_id,),
        )
        if row is None:
            return None
        price_path, activity_stream, transcript, previous_negotiations = await _load_agent_children(conn, agent_id)
        return _serialize_agent(
            row,
            price_path=price_path,
            activity_stream=activity_stream,
            transcript=transcript,
            previous_negotiations=previous_negotiations,
        )
    finally:
        await conn.close()


async def get_events(enterprise_id: str, status: str | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT id, enterprise_id, name, location, start_date, end_date, attendees, service, status, requirements, budget_per_person
        FROM galileo_events
        WHERE enterprise_id = ?
    """
    params: list[Any] = [enterprise_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY start_date DESC, id DESC"

    conn = await _connect()
    try:
        event_rows = await _fetchall(conn, query, tuple(params))
        if not event_rows:
            return []

        event_ids = [row["id"] for row in event_rows]
        placeholders = ",".join("?" for _ in event_ids)
        agent_rows = await _fetchall(
            conn,
            f"""
            SELECT id, enterprise_id, event_id, company_name, company_id, status,
                   outcome, ideal_price, ceiling_price, market_price, current_price, is_accepted
            FROM galileo_agents
            WHERE event_id IN ({placeholders})
            ORDER BY rowid DESC
            """,
            tuple(event_ids),
        )
        agents_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in agent_rows:
            agents_by_event[row["event_id"]].append(_serialize_agent(row))

        return [
            _serialize_event(row, agents=agents_by_event.get(row["id"], []))
            for row in event_rows
        ]
    finally:
        await conn.close()


async def get_event(event_id: str) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        event_row = await _fetchone(
            conn,
            """
            SELECT id, enterprise_id, name, location, start_date, end_date, attendees, service, status, requirements, budget_per_person
            FROM galileo_events
            WHERE id = ?
            """,
            (event_id,),
        )
        if event_row is None:
            return None

        agent_rows = await _fetchall(
            conn,
            """
            SELECT id, enterprise_id, event_id, company_name, company_id, status,
                   outcome, ideal_price, ceiling_price, market_price, current_price, is_accepted
            FROM galileo_agents
            WHERE event_id = ?
            ORDER BY rowid DESC
            """,
            (event_id,),
        )

        agents: list[dict[str, Any]] = []
        for row in agent_rows:
            price_path, activity_stream, transcript, previous_negotiations = await _load_agent_children(conn, row["id"])
            agents.append(
                _serialize_agent(
                    row,
                    price_path=price_path,
                    activity_stream=activity_stream,
                    transcript=transcript,
                    previous_negotiations=previous_negotiations,
                )
            )

        payload = _serialize_event(event_row, agents=agents)
        if str(event_row["status"]).lower() == "completed":
            winner = next((agent for agent in agents if agent["isAccepted"]), None)
            if winner:
                payload["winnerAgentId"] = winner["id"]
                payload["winnerTranscript"] = winner["transcript"]
                payload["winnerPricePath"] = winner["pricePath"]
        return payload
    finally:
        await conn.close()


async def get_companies(q: str | None = None, limit: int | None = 100) -> list[dict[str, Any]]:
    limit_value = max(1, min(limit or 100, 500))
    query = """
        SELECT id, name, initials, description, phone, website, industry, badge
        FROM galileo_companies
    """
    params: list[Any] = []
    if q:
        query += " WHERE lower(name) LIKE ?"
        params.append(f"%{q.lower()}%")
    query += " ORDER BY name ASC LIMIT ?"
    params.append(limit_value)

    conn = await _connect()
    try:
        rows = await _fetchall(conn, query, tuple(params))
        return [_serialize_company(row) for row in rows if _serialize_company(row) is not None]
    finally:
        await conn.close()


async def get_company(company_id: str) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        company_row = await _fetchone(
            conn,
            """
            SELECT id, name, initials, description, phone, website, industry, badge
            FROM galileo_companies
            WHERE id = ?
            """,
            (company_id,),
        )
        if company_row is None:
            return None

        location_rows = await _fetchall(
            conn,
            """
            SELECT id, company_id, name, address, phone
            FROM galileo_locations
            WHERE company_id = ?
            ORDER BY name ASC
            """,
            (company_id,),
        )
        locations = [_serialize_location(row) for row in location_rows]
        return _serialize_company(company_row, locations=locations)
    finally:
        await conn.close()


async def get_enterprise_company_view(
    enterprise_id: str,
    company_id: str,
    location_id: str | None = None,
) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        company_row = await _fetchone(
            conn,
            """
            SELECT id, name
            FROM galileo_companies
            WHERE id = ?
            """,
            (company_id,),
        )
        if company_row is None:
            return None

        location_name: str | None = None
        if location_id:
            location_row = await _fetchone(
                conn,
                """
                SELECT id, name
                FROM galileo_locations
                WHERE id = ? AND company_id = ?
                """,
                (location_id, company_id),
            )
            if location_row is None:
                return None
            location_name = location_row["name"]

        query = """
            SELECT a.id AS agent_id,
                   a.event_id,
                   a.status,
                   a.ideal_price,
                   a.market_price,
                   a.current_price,
                   a.is_accepted,
                   e.name AS event_name,
                   e.location AS event_location,
                   e.start_date,
                   e.end_date,
                   e.service,
                   e.status AS event_status,
                   e.attendees
            FROM galileo_agents a
            JOIN galileo_events e ON e.id = a.event_id
            WHERE a.enterprise_id = ?
              AND a.company_id = ?
              AND (a.is_accepted = 1 OR a.status = ?)
        """
        params: list[Any] = [enterprise_id, company_id, GALILEO_NEGOTIATING_STATUS]
        if location_name:
            # Match event location (city string like "Chicago, IL") against
            # location id, location name, or city portion of the location address
            location_row_full = await _fetchone(
                conn,
                "SELECT address FROM galileo_locations WHERE id = ? AND company_id = ?",
                (location_id, company_id),
            )
            location_address = location_row_full["address"] if location_row_full else None
            query += " AND (e.location = ? OR e.location = ?"
            params.extend([location_id, location_name])
            if location_address:
                # e.location is "Chicago, IL"; address is "230 W Kinzie St, Chicago, IL 60654"
                query += " OR ? LIKE '%' || e.location || '%'"
                params.append(location_address)
            query += ")"
        query += " ORDER BY e.start_date DESC, a.rowid DESC"

        linked_rows = await _fetchall(conn, query, tuple(params))

        if not linked_rows:
            return {
                "companyId": company_id,
                "enterpriseId": enterprise_id,
                "locationId": location_id,
                "lifetimeSavings": 0.0,
                "savingsDelta": 0.0,
                "agreementsCount": 0,
                "agreementsSummary": "No linked agreements",
                "avgDelta": 0.0,
                "totalBookings": 0,
                "totalSavings": 0.0,
                "yoyChange": 0.0,
                "pricingTrends": [],
                "bookingWindow": [],
                "linkedEvents": [],
            }

        accepted_rows = [row for row in linked_rows if row["is_accepted"]]
        deltas = [
            (((float(row["market_price"] or 0) - float(row["current_price"] or 0)) / float(row["market_price"] or 1)) * 100)
            if float(row["market_price"] or 0) > 0 else 0.0
            for row in linked_rows
        ]
        total_savings = sum(
            max(0.0, float(row["market_price"] or 0) - float(row["current_price"] or 0))
            for row in accepted_rows
        )
        lifetime_savings = sum(
            max(0.0, float(row["market_price"] or 0) - float(row["current_price"] or 0))
            for row in linked_rows
        )
        potential_savings = sum(
            max(0.0, float(row["market_price"] or 0) - float(row["ideal_price"] or 0))
            for row in linked_rows
        )
        total_bookings = len({row["event_id"] for row in linked_rows})
        agreements_count = len(accepted_rows)

        savings_delta = 0.0
        if potential_savings > 0:
            savings_delta = (total_savings / potential_savings) * 100
        avg_delta = sum(deltas) / len(deltas) if deltas else 0.0

        current_year = datetime.now(UTC).year
        current_year_savings = 0.0
        previous_year_savings = 0.0
        for row in accepted_rows:
            start_date = str(row["start_date"] or "")
            year = None
            try:
                year = datetime.fromisoformat(start_date.replace("Z", "+00:00")).year
            except ValueError:
                if len(start_date) >= 4 and start_date[:4].isdigit():
                    year = int(start_date[:4])
            if year == current_year:
                current_year_savings += max(0.0, float(row["market_price"] or 0) - float(row["current_price"] or 0))
            elif year == (current_year - 1):
                previous_year_savings += max(0.0, float(row["market_price"] or 0) - float(row["current_price"] or 0))
        if previous_year_savings > 0:
            yoy_change = ((current_year_savings - previous_year_savings) / previous_year_savings) * 100
        elif current_year_savings > 0:
            yoy_change = 100.0
        else:
            yoy_change = 0.0

        trend_buckets: dict[tuple[int, int], dict[str, Any]] = {}
        for row in linked_rows:
            start_date = str(row["start_date"] or "")
            dt: datetime | None = None
            try:
                dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            except ValueError:
                if len(start_date) >= 10:
                    try:
                        dt = datetime.strptime(start_date[:10], "%Y-%m-%d")
                    except ValueError:
                        dt = None
            if dt is None:
                continue
            key = (dt.year, dt.month)
            bucket = trend_buckets.setdefault(
                key,
                {
                    "month": dt.strftime("%b"),
                    "year": dt.year,
                    "negotiated": [],
                    "market": [],
                },
            )
            bucket["negotiated"].append(float(row["current_price"] or 0))
            bucket["market"].append(float(row["market_price"] or 0))

        pricing_trends_all: list[dict[str, Any]] = []
        pricing_trends_1y: list[dict[str, Any]] = []
        booking_window: list[dict[str, Any]] = []
        one_year_cutoff = datetime.now(UTC).replace(day=1)
        one_year_cutoff = one_year_cutoff.replace(year=one_year_cutoff.year - 1)
        for (year, month) in sorted(trend_buckets.keys()):
            bucket = trend_buckets[(year, month)]
            negotiated = sum(bucket["negotiated"]) / len(bucket["negotiated"]) if bucket["negotiated"] else 0.0
            market = sum(bucket["market"]) / len(bucket["market"]) if bucket["market"] else 0.0
            spread_pct = ((market - negotiated) / market) * 100 if market > 0 else 0
            if spread_pct >= 12:
                status = "Best Deal"
            elif spread_pct >= 6:
                status = "Good"
            else:
                status = "Peak"
            score = max(0.0, min(100.0, 50.0 + (spread_pct * 3)))
            trend_point = {
                "month": bucket["month"].upper(),
                "year": year,
                "negotiatedPrice": round(negotiated, 2),
                "marketPrice": round(market, 2),
            }
            pricing_trends_all.append({**trend_point, "range": "ALL"})
            bucket_dt = datetime(year, month, 1, tzinfo=UTC)
            if bucket_dt >= one_year_cutoff:
                pricing_trends_1y.append({**trend_point, "range": "1Y"})
            booking_window.append(
                {
                    "month": bucket["month"].upper(),
                    "score": round(score, 1),
                    "status": status,
                }
            )

        event_ids = list({row["event_id"] for row in linked_rows})
        event_placeholders = ",".join("?" for _ in event_ids)
        event_rows = await _fetchall(
            conn,
            f"""
            SELECT id, enterprise_id, name, location, start_date, end_date, attendees, service, status, requirements, budget_per_person
            FROM galileo_events
            WHERE id IN ({event_placeholders})
            ORDER BY start_date DESC, id DESC
            """,
            tuple(event_ids),
        )
        agent_rows = await _fetchall(
            conn,
            f"""
            SELECT id, enterprise_id, event_id, company_name, company_id, status,
                   outcome, ideal_price, ceiling_price, market_price, current_price, is_accepted
            FROM galileo_agents
            WHERE event_id IN ({event_placeholders})
            ORDER BY rowid DESC
            """,
            tuple(event_ids),
        )
        agents_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in agent_rows:
            agents_by_event[row["event_id"]].append(_serialize_agent(row))
        linked_events = [
            _serialize_event(row, agents=agents_by_event.get(row["id"], []))
            for row in event_rows
        ]

        return {
            "companyId": company_id,
            "enterpriseId": enterprise_id,
            "locationId": location_id,
            "lifetimeSavings": round(lifetime_savings, 2),
            "savingsDelta": round(savings_delta, 2),
            "agreementsCount": agreements_count,
            "agreementsSummary": f"{agreements_count} accepted agreements across {total_bookings} linked events",
            "avgDelta": round(avg_delta, 2),
            "totalBookings": total_bookings,
            "totalSavings": round(total_savings, 2),
            "yoyChange": round(yoy_change, 2),
            "pricingTrends": pricing_trends_1y + pricing_trends_all,
            "bookingWindow": booking_window,
            "linkedEvents": linked_events,
        }
    finally:
        await conn.close()


async def accept_offer(event_id: str, agent_id: str, enterprise_id: str) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        target = await _fetchone(
            conn,
            """
            SELECT id, enterprise_id, event_id, is_accepted, status, market_price, current_price
            FROM galileo_agents
            WHERE id = ? AND event_id = ? AND enterprise_id = ?
            """,
            (agent_id, event_id, enterprise_id),
        )
        if target is None:
            await conn.rollback()
            return None

        if not bool(target["is_accepted"]):
            savings_increment = max(0.0, float(target["market_price"] or 0) - float(target["current_price"] or 0))

            await conn.execute(
                """
                UPDATE galileo_agents
                SET is_accepted = 1,
                    status = 'Completed',
                    outcome = COALESCE(outcome, 'RATE_CONFIRMED')
                WHERE id = ?
                """,
                (agent_id,),
            )
            prev_accepted = await _fetchone(
                conn,
                """
                SELECT id, market_price, current_price
                FROM galileo_agents
                WHERE event_id = ?
                  AND id <> ?
                  AND is_accepted = 1
                """,
                (event_id, agent_id),
            )
            prev_savings = 0.0
            if prev_accepted:
                prev_savings = max(
                    0.0,
                    float(prev_accepted["market_price"] or 0) - float(prev_accepted["current_price"] or 0),
                )

            await conn.execute(
                """
                UPDATE galileo_agents
                SET is_accepted = 0,
                    status = 'Cancelled',
                    outcome = CASE
                        WHEN outcome IS NULL THEN 'TIMED_OUT'
                        ELSE outcome
                    END
                WHERE event_id = ?
                  AND id <> ?
                """,
                (event_id, agent_id),
            )

            net_savings = savings_increment - prev_savings

            await conn.execute(
                """
                UPDATE galileo_enterprises
                SET total_saved = COALESCE(total_saved, 0) + ?,
                    total_saved_hotels = COALESCE(total_saved_hotels, 0) + ?,
                    hotel_contract_count = COALESCE(hotel_contract_count, 0) + ?
                WHERE id = ?
                """,
                (net_savings, net_savings, 1, enterprise_id),
            )
        await conn.execute(
            """
            UPDATE galileo_events
            SET status = 'Completed'
            WHERE id = ?
            """,
            (event_id,),
        )

        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()

    return await get_event(event_id)


async def create_event_with_agents(launch_request: Any) -> dict[str, Any] | None:
    payload: dict[str, Any]
    if isinstance(launch_request, dict):
        payload = dict(launch_request)
    elif hasattr(launch_request, "model_dump"):
        payload = launch_request.model_dump(exclude_none=True)
    else:
        payload = dict(vars(launch_request))

    enterprise_id = _get_value(payload, "enterpriseId", "enterprise_id")
    if not enterprise_id:
        return None

    event_id = str(uuid4())
    raw_service = _get_value(payload, "service", default="Hotel")
    service = raw_service.value if hasattr(raw_service, "value") else str(raw_service)
    event_name = str(_get_value(payload, "eventName", "event_name", default="New Galileo Event"))
    event_location = _get_value(payload, "location", default="")
    start_date = _get_value(payload, "startDate", "start_date")
    end_date = _get_value(payload, "endDate", "end_date")
    attendees = int(_get_value(payload, "attendees", default=0) or 0)
    budget_per_person = _get_value(payload, "budgetPerPerson", "budget_per_person")
    requirements = _coerce_json_text(_get_value(payload, "requirements"))
    guardrails = _get_value(payload, "guardrails", default={}) or {}
    # Top-level idealPrice/ceilingPrice override guardrails
    top_ideal = _get_value(payload, "idealPrice", "ideal_price")
    top_ceiling = _get_value(payload, "ceilingPrice", "ceiling_price")

    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        await conn.execute(
            """
            INSERT INTO galileo_events (
                id, enterprise_id, name, location, start_date, end_date, attendees,
                service, status, requirements, budget_per_person
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?)
            """,
            (
                event_id,
                enterprise_id,
                event_name,
                event_location,
                start_date,
                end_date,
                attendees,
                service,
                requirements,
                budget_per_person,
            ),
        )

        # Prefer companies with a location matching the event location
        if event_location:
            company_rows = await _fetchall(
                conn,
                """
                SELECT DISTINCT c.id, c.name, c.industry, c.description
                FROM galileo_companies c
                JOIN galileo_locations l ON l.company_id = c.id
                WHERE l.address LIKE ? OR l.name LIKE ?
                ORDER BY c.name ASC
                """,
                (f"%{event_location}%", f"%{event_location}%"),
            )
        else:
            company_rows = []

        if not company_rows:
            company_rows = await _fetchall(
                conn,
                """
                SELECT id, name, industry, description
                FROM galileo_companies
                ORDER BY name ASC
                """,
            )
        if not company_rows:
            await conn.commit()
            return await get_event(event_id)

        required_types = _required_service_types(service)
        for service_type in required_types:
            matching_rows = [row for row in company_rows if _company_matches_service(row, service_type)]
            selected_rows = matching_rows[:3] if matching_rows else company_rows[:2]
            for idx, company in enumerate(selected_rows):
                lower_type = service_type.lower()
                type_guardrails = guardrails.get(lower_type, {}) if isinstance(guardrails, dict) else {}
                # Top-level idealPrice/ceilingPrice take priority over nested guardrails
                ideal_price = float(
                    top_ideal
                    or type_guardrails.get("idealPrice")
                    or type_guardrails.get("ideal_price")
                    or (budget_per_person or 0)
                )
                if ideal_price <= 0:
                    minimum, maximum = _price_range_for_service(service_type)
                    ideal_price = round(random.uniform(minimum, maximum), 2)

                ceiling_price = float(
                    top_ceiling
                    or type_guardrails.get("ceilingPrice")
                    or type_guardrails.get("ceiling_price")
                    or (ideal_price * 1.15)
                )
                if ceiling_price < ideal_price:
                    ceiling_price = round(ideal_price * 1.1, 2)

                minimum, maximum = _price_range_for_service(service_type)
                original_price = round(random.uniform(max(ideal_price, minimum), max(ceiling_price, maximum)), 2)
                current_price = round(original_price * random.uniform(0.92, 0.99), 2)
                agent_status = GALILEO_NEGOTIATING_STATUS if idx == 0 else "Queued"

                agent_id = str(uuid4())
                await conn.execute(
                    """
                    INSERT INTO galileo_agents (
                        id, enterprise_id, event_id, company_name, company_id, status, outcome,
                        ideal_price, ceiling_price, market_price, current_price, is_accepted
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 0)
                    """,
                    (
                        agent_id,
                        enterprise_id,
                        event_id,
                        company["name"],
                        company["id"],
                        agent_status,
                        ideal_price,
                        ceiling_price,
                        original_price,
                        current_price,
                    ),
                )

                await conn.execute(
                    """
                    INSERT INTO galileo_price_points (agent_id, label, price, type, round)
                    VALUES (?, 'Opening', ?, 'offer', 1)
                    """,
                    (agent_id, original_price),
                )
                await conn.execute(
                    """
                    INSERT INTO galileo_activity_stream (
                        id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        agent_id,
                        current_price,
                        "System",
                        "neutral",
                        "Negotiation initialized",
                        "neutral",
                        _now_iso(),
                        1 if agent_status == GALILEO_NEGOTIATING_STATUS else 0,
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO galileo_messages (id, agent_id, message, sender, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        agent_id,
                        "Galileo prepared opening outreach.",
                        "Galileo",
                        _now_iso(),
                    ),
                )
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()

    return await get_event(event_id)


async def intervene_agent(agent_id: str) -> dict[str, Any] | None:
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        row = await _fetchone(
            conn,
            """
            SELECT id, status
            FROM galileo_agents
            WHERE id = ?
            """,
            (agent_id,),
        )
        if row is None:
            await conn.rollback()
            return None

        transferred_at = _now_iso()
        await conn.execute(
            """
            UPDATE galileo_agents
            SET status = 'Reviewing',
                outcome = CASE
                    WHEN outcome IS NULL THEN 'ESCALATED_TO_HUMAN'
                    ELSE outcome
                END
            WHERE id = ?
            """,
            (agent_id,),
        )
        await conn.execute(
            """
            INSERT INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            )
            VALUES (?, ?, 0, ?, ?, ?, ?, ?, 1)
            """,
            (
                str(uuid4()),
                agent_id,
                "Manual Intervention",
                "neutral",
                "Agent routed to human representative",
                "neutral",
                transferred_at,
            ),
        )

        await conn.commit()
        return {
            "agentId": agent_id,
            "status": "Routed",
            "callRoutingInfo": "+1-800-555-0147 conference bridge",
            "transferredAt": transferred_at,
        }
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def record_price_change(
    agent_id: str,
    price: float,
    source: str,
    round_num: int | None = None,
) -> dict[str, Any] | None:
    """Record a confirmed price change during negotiation.

    Args:
        agent_id: The galileo agent id.
        price: The new price per night.
        source: Who proposed it -- "galileo" or "hotel_rep".
        round_num: Optional explicit round number. Auto-increments if omitted.
    """
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")

        row = await _fetchone(
            conn,
            "SELECT id, current_price, market_price FROM galileo_agents WHERE id = ?",
            (agent_id,),
        )
        if row is None:
            await conn.rollback()
            return None

        previous_price = float(row["current_price"] or 0)
        market_price = float(row["market_price"] or 0)

        # Update current_price on the agent
        await conn.execute(
            "UPDATE galileo_agents SET current_price = ? WHERE id = ?",
            (price, agent_id),
        )

        # Determine round
        if round_num is None:
            max_row = await _fetchone(
                conn,
                "SELECT COALESCE(MAX(round), 0) AS max_round FROM galileo_price_points WHERE agent_id = ?",
                (agent_id,),
            )
            round_num = (max_row["max_round"] if max_row else 0) + 1

        # Insert price point
        label = "Galileo Counter" if source == "galileo" else "Hotel Offer"
        pp_type = "negotiated" if source == "galileo" else "offer"
        await conn.execute(
            """
            INSERT INTO galileo_price_points (agent_id, label, price, type, round)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent_id, label, price, pp_type, round_num),
        )

        # Activity stream entry
        direction = "down" if price < previous_price else "up"
        badge_type = "savings" if price < previous_price else "neutral"
        source_label = "Galileo" if source == "galileo" else "Hotel Rep"
        detail = f"${price:.2f}/night ({source_label})"
        if previous_price > 0:
            delta = previous_price - price
            detail += f" | {'saved' if delta > 0 else 'increased'} ${abs(delta):.2f}"

        activity_id = str(uuid4())
        await conn.execute(
            """
            INSERT INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                activity_id,
                agent_id,
                price,
                f"Price {direction.title()}",
                badge_type,
                detail,
                "positive" if price < previous_price else "negative",
                _now_iso(),
            ),
        )

        await conn.commit()
        return {
            "agent_id": agent_id,
            "price": price,
            "previous_price": previous_price,
            "market_price": market_price,
            "source": source,
            "round": round_num,
            "activity_id": activity_id,
        }
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def record_final_offer(
    agent_id: str,
    final_price: float,
    enterprise_id: str,
) -> dict[str, Any] | None:
    """Record the final offer once a deal is closed.

    Updates the agent status to Completed, marks outcome as RATE_CONFIRMED,
    inserts a final price point, and updates enterprise savings.
    """
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")

        row = await _fetchone(
            conn,
            """
            SELECT id, event_id, market_price, current_price, is_accepted
            FROM galileo_agents WHERE id = ?
            """,
            (agent_id,),
        )
        if row is None:
            await conn.rollback()
            return None

        market_price = float(row["market_price"] or 0)
        savings = max(0.0, market_price - final_price)

        # Update agent
        await conn.execute(
            """
            UPDATE galileo_agents
            SET current_price = ?,
                status = 'Completed',
                outcome = 'RATE_CONFIRMED',
                is_accepted = 1
            WHERE id = ?
            """,
            (final_price, agent_id),
        )

        # Final price point
        max_row = await _fetchone(
            conn,
            "SELECT COALESCE(MAX(round), 0) AS max_round FROM galileo_price_points WHERE agent_id = ?",
            (agent_id,),
        )
        next_round = (max_row["max_round"] if max_row else 0) + 1
        await conn.execute(
            """
            INSERT INTO galileo_price_points (agent_id, label, price, type, round)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent_id, "Final Accepted", final_price, "final", next_round),
        )

        # Activity stream
        activity_id = str(uuid4())
        await conn.execute(
            """
            INSERT INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                activity_id,
                agent_id,
                final_price,
                "Deal Closed",
                "savings",
                f"Final rate: ${final_price:.2f}/night | Saved ${savings:.2f} vs market",
                "positive",
                _now_iso(),
            ),
        )

        # Update enterprise savings
        if enterprise_id:
            await conn.execute(
                """
                UPDATE galileo_enterprises
                SET total_saved = COALESCE(total_saved, 0) + ?,
                    total_saved_hotels = COALESCE(total_saved_hotels, 0) + ?,
                    hotel_contract_count = COALESCE(hotel_contract_count, 0) + 1
                WHERE id = ?
                """,
                (savings, savings, enterprise_id),
            )

        await conn.commit()
        return {
            "agent_id": agent_id,
            "final_price": final_price,
            "market_price": market_price,
            "savings": savings,
            "activity_id": activity_id,
        }
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def update_agent_call_result(
    agent_id: str,
    status: str,
    outcome: str | None = None,
    current_price: float | None = None,
) -> bool:
    """Update a galileo_agent row with the call outcome and add an activity entry."""
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")

        row = await _fetchone(
            conn,
            "SELECT id FROM galileo_agents WHERE id = ?",
            (agent_id,),
        )
        if row is None:
            await conn.rollback()
            return False

        set_clauses = ["status = ?"]
        params: list[Any] = [status]
        if outcome is not None:
            set_clauses.append("outcome = ?")
            params.append(outcome)
        if current_price is not None:
            set_clauses.append("current_price = ?")
            params.append(current_price)
        params.append(agent_id)

        await conn.execute(
            f"UPDATE galileo_agents SET {', '.join(set_clauses)} WHERE id = ?",
            tuple(params),
        )

        # Determine badge text and type from outcome
        _badge_map: dict[str, tuple[str, str]] = {
            "rate_confirmed": ("Rate Confirmed", "positive"),
            "callback_requested": ("Callback Requested", "neutral"),
            "no_availability": ("No Availability", "negative"),
            "escalated_to_human": ("Escalated to Human", "neutral"),
            "failed": ("Call Failed", "negative"),
            "timed_out": ("Timed Out", "negative"),
        }
        badge, badge_type = _badge_map.get(outcome or "", ("Call Ended", "neutral"))
        detail = f"Final rate: ${current_price:.2f}/night" if current_price else f"Outcome: {outcome or status}"
        detail_type = badge_type

        await conn.execute(
            """
            INSERT INTO galileo_activity_stream (
                id, agent_id, price, badge, badge_type, detail, detail_type, timestamp, active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                str(uuid4()),
                agent_id,
                current_price or 0,
                badge,
                badge_type,
                detail,
                detail_type,
                _now_iso(),
            ),
        )

        # Add a final price point if we have a price
        if current_price is not None:
            max_round = await _fetchone(
                conn,
                "SELECT COALESCE(MAX(round), 0) AS max_round FROM galileo_price_points WHERE agent_id = ?",
                (agent_id,),
            )
            next_round = (max_round["max_round"] if max_round else 0) + 1
            await conn.execute(
                """
                INSERT INTO galileo_price_points (agent_id, label, price, type, round)
                VALUES (?, ?, ?, ?, ?)
                """,
                (agent_id, "Final", current_price, "final", next_round),
            )

        await conn.commit()
        return True
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def delete_event(event_id: str) -> bool:
    conn = await _connect()
    try:
        await conn.execute("BEGIN IMMEDIATE")
        agent_rows = await _fetchall(
            conn,
            "SELECT id FROM galileo_agents WHERE event_id = ?",
            (event_id,),
        )
        for row in agent_rows:
            aid = row["id"]
            await conn.execute("DELETE FROM galileo_price_points WHERE agent_id = ?", (aid,))
            await conn.execute("DELETE FROM galileo_activity_stream WHERE agent_id = ?", (aid,))
            await conn.execute("DELETE FROM galileo_messages WHERE agent_id = ?", (aid,))

        await conn.execute("DELETE FROM galileo_agents WHERE event_id = ?", (event_id,))
        result = await conn.execute("DELETE FROM galileo_events WHERE id = ?", (event_id,))
        deleted = result.rowcount > 0
        await conn.commit()
        return deleted
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()

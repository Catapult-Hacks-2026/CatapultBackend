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
        "segment": row["segment"],
        "type": row["type"],
        "status": row["status"],
        "lifecycleStatus": row["lifecycle_status"],
        "outcome": row["outcome"],
        "idealPrice": float(row["ideal_price"] or 0),
        "ceilingPrice": float(row["ceiling_price"] or 0),
        "originalPrice": float(row["original_price"] or 0),
        "currentPrice": float(row["current_price"] or 0),
        "delta": float(row["delta"] or 0),
        "potentialSavings": float(row["potential_savings"] or 0),
        "savingsToDate": float(row["savings_to_date"] or 0),
        "distanceToGoal": float(row["distance_to_goal"] or 0),
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
    if normalized == "hotel":
        return "hotel" in searchable or "hospitality" in searchable or "resort" in searchable
    if normalized == "airline":
        return "airline" in searchable or "aviation" in searchable or "airways" in searchable
    return True


def _required_service_types(service: str) -> list[str]:
    normalized = (service or "").strip().lower()
    if normalized == "both":
        return ["Hotel", "Airline"]
    if normalized == "airline":
        return ["Airline"]
    return ["Hotel"]


def _is_event_complete(event_service: str, accepted_types: set[str]) -> bool:
    required = {value.lower() for value in _required_service_types(event_service)}
    return required.issubset({value.lower() for value in accepted_types})


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
                company_id TEXT NOT NULL REFERENCES galileo_companies(id),
                company_name TEXT,
                segment TEXT,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Queued',
                lifecycle_status TEXT NOT NULL DEFAULT 'INITIALIZING',
                outcome TEXT,
                ideal_price REAL,
                ceiling_price REAL,
                original_price REAL,
                current_price REAL,
                delta REAL,
                potential_savings REAL,
                savings_to_date REAL,
                distance_to_goal REAL,
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

            CREATE INDEX IF NOT EXISTS idx_galileo_agents_event_type
            ON galileo_agents (event_id, type);

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
        SELECT id, enterprise_id, event_id, company_id, company_name, segment, type, status, lifecycle_status,
               outcome, ideal_price, ceiling_price, original_price, current_price, delta, potential_savings,
               savings_to_date, distance_to_goal, is_accepted
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
            SELECT id, enterprise_id, event_id, company_id, company_name, segment, type, status, lifecycle_status,
                   outcome, ideal_price, ceiling_price, original_price, current_price, delta, potential_savings,
                   savings_to_date, distance_to_goal, is_accepted
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
            SELECT id, enterprise_id, event_id, company_id, company_name, segment, type, status, lifecycle_status,
                   outcome, ideal_price, ceiling_price, original_price, current_price, delta, potential_savings,
                   savings_to_date, distance_to_goal, is_accepted
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
            SELECT id, enterprise_id, event_id, company_id, company_name, segment, type, status, lifecycle_status,
                   outcome, ideal_price, ceiling_price, original_price, current_price, delta, potential_savings,
                   savings_to_date, distance_to_goal, is_accepted
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
                   a.type,
                   a.status,
                   a.delta,
                   a.original_price,
                   a.current_price,
                   a.potential_savings,
                   a.savings_to_date,
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
        deltas = [float(row["delta"] or 0) for row in linked_rows]
        total_savings = sum(float(row["savings_to_date"] or 0) for row in linked_rows)
        lifetime_savings = sum(float(row["savings_to_date"] or row["potential_savings"] or 0) for row in linked_rows)
        potential_savings = sum(float(row["potential_savings"] or 0) for row in linked_rows)
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
                current_year_savings += float(row["savings_to_date"] or 0)
            elif year == (current_year - 1):
                previous_year_savings += float(row["savings_to_date"] or 0)
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
            bucket["market"].append(float(row["original_price"] or 0))

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
            SELECT id, enterprise_id, event_id, company_id, company_name, segment, type, status, lifecycle_status,
                   outcome, ideal_price, ceiling_price, original_price, current_price, delta, potential_savings,
                   savings_to_date, distance_to_goal, is_accepted
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
            SELECT id, enterprise_id, event_id, type, is_accepted, status, savings_to_date, potential_savings,
                   original_price, current_price
            FROM galileo_agents
            WHERE id = ? AND event_id = ? AND enterprise_id = ?
            """,
            (agent_id, event_id, enterprise_id),
        )
        if target is None:
            await conn.rollback()
            return None

        if not bool(target["is_accepted"]):
            savings_increment = float(target["savings_to_date"] or 0)
            if savings_increment <= 0:
                savings_increment = float(target["potential_savings"] or 0)
            if savings_increment <= 0:
                original_price = float(target["original_price"] or 0)
                current_price = float(target["current_price"] or 0)
                savings_increment = max(0.0, original_price - current_price)

            await conn.execute(
                """
                UPDATE galileo_agents
                SET is_accepted = 1,
                    status = 'Completed',
                    lifecycle_status = 'COMPLETED',
                    outcome = COALESCE(outcome, 'RATE_CONFIRMED'),
                    savings_to_date = CASE
                        WHEN savings_to_date IS NULL OR savings_to_date = 0 THEN ?
                        ELSE savings_to_date
                    END
                WHERE id = ?
                """,
                (savings_increment, agent_id),
            )

            # Find any previously accepted agent of the same type to reverse its totals
            prev_accepted = await _fetchone(
                conn,
                """
                SELECT id, savings_to_date, potential_savings, original_price, current_price
                FROM galileo_agents
                WHERE event_id = ?
                  AND id <> ?
                  AND lower(type) = lower(?)
                  AND is_accepted = 1
                """,
                (event_id, agent_id, target["type"]),
            )
            prev_savings = 0.0
            if prev_accepted:
                prev_savings = float(prev_accepted["savings_to_date"] or 0)
                if prev_savings <= 0:
                    prev_savings = float(prev_accepted["potential_savings"] or 0)
                if prev_savings <= 0:
                    prev_orig = float(prev_accepted["original_price"] or 0)
                    prev_curr = float(prev_accepted["current_price"] or 0)
                    prev_savings = max(0.0, prev_orig - prev_curr)

            await conn.execute(
                """
                UPDATE galileo_agents
                SET is_accepted = 0,
                    status = 'Cancelled',
                    lifecycle_status = CASE
                        WHEN lifecycle_status = 'COMPLETED' THEN lifecycle_status
                        ELSE 'FAILED'
                    END,
                    outcome = CASE
                        WHEN outcome IS NULL THEN 'TIMED_OUT'
                        ELSE outcome
                    END
                WHERE event_id = ?
                  AND id <> ?
                  AND lower(type) = lower(?)
                """,
                (event_id, agent_id, target["type"]),
            )

            type_lower = str(target["type"] or "").strip().lower()
            net_savings = savings_increment - prev_savings
            hotels_delta = net_savings if type_lower == "hotel" else 0.0
            airlines_delta = net_savings if type_lower == "airline" else 0.0
            hotel_count_delta = (1 if type_lower == "hotel" else 0) - (1 if prev_accepted and type_lower == "hotel" else 0)
            airline_count_delta = (1 if type_lower == "airline" else 0) - (1 if prev_accepted and type_lower == "airline" else 0)

            await conn.execute(
                """
                UPDATE galileo_enterprises
                SET total_saved = COALESCE(total_saved, 0) + ?,
                    total_saved_hotels = COALESCE(total_saved_hotels, 0) + ?,
                    total_saved_airlines = COALESCE(total_saved_airlines, 0) + ?,
                    hotel_contract_count = COALESCE(hotel_contract_count, 0) + ?,
                    airline_contract_count = COALESCE(airline_contract_count, 0) + ?
                WHERE id = ?
                """,
                (net_savings, hotels_delta, airlines_delta, hotel_count_delta, airline_count_delta, enterprise_id),
            )

        accepted_type_rows = await _fetchall(
            conn,
            """
            SELECT DISTINCT type
            FROM galileo_agents
            WHERE event_id = ? AND is_accepted = 1
            """,
            (event_id,),
        )
        accepted_types = {str(row["type"]) for row in accepted_type_rows if row["type"] is not None}
        event_row = await _fetchone(
            conn,
            """
            SELECT service
            FROM galileo_events
            WHERE id = ?
            """,
            (event_id,),
        )
        if event_row is not None and _is_event_complete(str(event_row["service"] or ""), accepted_types):
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
    service = str(_get_value(payload, "service", default="Hotel"))
    event_name = str(_get_value(payload, "eventName", "event_name", default="New Galileo Event"))
    event_location = _get_value(payload, "location", default="")
    start_date = _get_value(payload, "startDate", "start_date")
    end_date = _get_value(payload, "endDate", "end_date")
    attendees = int(_get_value(payload, "attendees", default=0) or 0)
    budget_per_person = _get_value(payload, "budgetPerPerson", "budget_per_person")
    requirements = _coerce_json_text(_get_value(payload, "requirements"))
    guardrails = _get_value(payload, "guardrails", default={}) or {}

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
                ideal_price = float(
                    type_guardrails.get("idealPrice")
                    or type_guardrails.get("ideal_price")
                    or (budget_per_person or 0)
                )
                if ideal_price <= 0:
                    minimum, maximum = _price_range_for_service(service_type)
                    ideal_price = round(random.uniform(minimum, maximum), 2)

                ceiling_price = float(
                    type_guardrails.get("ceilingPrice")
                    or type_guardrails.get("ceiling_price")
                    or (ideal_price * 1.15)
                )
                if ceiling_price < ideal_price:
                    ceiling_price = round(ideal_price * 1.1, 2)

                minimum, maximum = _price_range_for_service(service_type)
                original_price = round(random.uniform(max(ideal_price, minimum), max(ceiling_price, maximum)), 2)
                current_price = round(original_price * random.uniform(0.92, 0.99), 2)
                savings = max(0.0, original_price - current_price)
                delta = round(((current_price - original_price) / original_price) * 100, 2) if original_price else 0.0
                distance_to_goal = max(0.0, current_price - ideal_price)

                agent_status = GALILEO_NEGOTIATING_STATUS if idx == 0 else "Queued"
                lifecycle_status = "ACTIVE" if idx == 0 else "INITIALIZING"

                agent_id = str(uuid4())
                await conn.execute(
                    """
                    INSERT INTO galileo_agents (
                        id, enterprise_id, event_id, company_id, company_name, segment, type, status,
                        lifecycle_status, outcome, ideal_price, ceiling_price, original_price, current_price,
                        delta, potential_savings, savings_to_date, distance_to_goal, is_accepted
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        agent_id,
                        enterprise_id,
                        event_id,
                        company["id"],
                        company["name"],
                        "Enterprise",
                        service_type,
                        agent_status,
                        lifecycle_status,
                        ideal_price,
                        ceiling_price,
                        original_price,
                        current_price,
                        delta,
                        max(0.0, original_price - ideal_price),
                        savings if agent_status == GALILEO_NEGOTIATING_STATUS else 0.0,
                        distance_to_goal,
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
                lifecycle_status = 'WRAPPING_UP',
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

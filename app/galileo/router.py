from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.core.config import get_settings

from app.galileo.database import (
    accept_offer as db_accept_offer,
    create_event_with_agents as db_create_event_with_agents,
    delete_event as db_delete_event,
    get_agent as db_get_agent,
    get_agents as db_get_agents,
    get_companies as db_get_companies,
    get_company as db_get_company,
    get_enterprise as db_get_enterprise,
    get_enterprise_company_view as db_get_enterprise_company_view,
    get_event as db_get_event,
    get_events as db_get_events,
    intervene_agent as db_intervene_agent,
)
from app.galileo.schemas import (
    AcceptOfferRequest,
    Agent,
    Company,
    Enterprise,
    EnterpriseCompanyView,
    EventWindowRequest,
    EventWindowResult,
    GalileoEvent,
    InterventionResult,
    LaunchNegotiationRequest,
    MarketPricingRequest,
    MarketPricingResult,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _days_between(start_date: str, end_date: str) -> int:
    try:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
    except ValueError:
        return 2
    return max((end - start).days, 1)


def _quarter_start_month(preferred_timing: str) -> int:
    upper = preferred_timing.upper()
    if "Q1" in upper:
        return 1
    if "Q2" in upper:
        return 4
    if "Q3" in upper:
        return 7
    if "Q4" in upper:
        return 10
    return max(min(date.today().month, 12), 1)


def _extract_year(preferred_timing: str) -> int:
    parts = preferred_timing.strip().split()
    for part in reversed(parts):
        if part.isdigit() and len(part) == 4:
            return int(part)
    return date.today().year


def _pricing_baseline(location: str, attendees: int, duration_days: int) -> tuple[float, float]:
    location_factor = (sum(ord(ch) for ch in location.lower()) % 37) - 18
    demand_factor = max(attendees, 1)
    hotel_market = 165.0 + (demand_factor * 0.35) + (duration_days * 4.0) + location_factor
    airline_market = 295.0 + (demand_factor * 0.45) + (duration_days * 7.0) + (location_factor * 1.5)
    return round(hotel_market, 2), round(airline_market, 2)


async def _require_agent(agent_id: str) -> dict[str, Any]:
    agent = await db_get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


async def _sse_stream(
    request: Request,
    agent_id: str,
    payload_key: str,
    event_name: str,
) -> EventSourceResponse:
    await _require_agent(agent_id)

    async def event_generator():
        sent_ids: set[str] = set()
        synthetic_id = 0

        while True:
            if await request.is_disconnected():
                break

            payload = await db_get_agent(agent_id)
            if not payload:
                break

            items = payload.get(payload_key)
            if items is None:
                snake_key = "".join(f"_{ch.lower()}" if ch.isupper() else ch for ch in payload_key).lstrip("_")
                items = payload.get(snake_key)
            items = items or []
            for item in items:
                synthetic_id += 1
                item_id = str(item.get("id") or synthetic_id)
                if item_id in sent_ids:
                    continue
                sent_ids.add(item_id)
                yield {
                    "event": event_name,
                    "id": item_id,
                    "data": json.dumps(item, default=str),
                }

            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator(), ping=15)


@router.get("/enterprises/{enterprise_id}", response_model=Enterprise)
async def get_enterprise(enterprise_id: str) -> Enterprise:
    enterprise = await db_get_enterprise(enterprise_id)
    if not enterprise:
        raise HTTPException(status_code=404, detail="Enterprise not found")
    return Enterprise.model_validate(enterprise)


@router.get("/enterprises/{enterprise_id}/agents", response_model=list[Agent])
async def get_agents(
    enterprise_id: str,
    status: str | None = Query(default=None),
    eventId: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[Agent]:
    agents = await db_get_agents(enterprise_id, status=status, event_id=eventId, limit=limit)
    return [Agent.model_validate(agent) for agent in agents]


@router.get("/agents/{agent_id}", response_model=Agent)
async def get_agent_detail(agent_id: str) -> Agent:
    agent = await db_get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return Agent.model_validate(agent)


@router.get("/enterprises/{enterprise_id}/events", response_model=list[GalileoEvent])
async def get_events(
    enterprise_id: str,
    status: str | None = Query(default=None),
) -> list[GalileoEvent]:
    events = await db_get_events(enterprise_id, status=status)
    return [GalileoEvent.model_validate(event) for event in events]


@router.get("/events/{event_id}", response_model=GalileoEvent)
async def get_event_detail(event_id: str) -> GalileoEvent:
    event = await db_get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return GalileoEvent.model_validate(event)


@router.delete("/events/{event_id}")
async def delete_event(event_id: str) -> dict:
    deleted = await db_delete_event(event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"id": event_id, "deleted": True}


@router.get("/companies", response_model=list[Company])
async def get_companies(
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Company]:
    companies = await db_get_companies(q=q, limit=limit)
    return [Company.model_validate(company) for company in companies]


@router.get("/companies/{company_id}", response_model=Company)
async def get_company_general(company_id: str) -> Company:
    company = await db_get_company(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return Company.model_validate(company)


@router.get(
    "/enterprises/{enterprise_id}/companies/{company_id}",
    response_model=EnterpriseCompanyView,
)
async def get_enterprise_company_view(
    enterprise_id: str,
    company_id: str,
    locationId: str | None = Query(default=None),
) -> EnterpriseCompanyView:
    view = await db_get_enterprise_company_view(enterprise_id, company_id, location_id=locationId)
    if not view:
        raise HTTPException(status_code=404, detail="Enterprise company view not found")
    return EnterpriseCompanyView.model_validate(view)


@router.post("/events/{event_id}/agents/{agent_id}/accept", response_model=GalileoEvent)
async def accept_offer(
    event_id: str,
    agent_id: str,
    payload: AcceptOfferRequest,
) -> GalileoEvent:
    updated_event = await db_accept_offer(event_id, agent_id, payload.enterpriseId)
    if not updated_event:
        raise HTTPException(status_code=404, detail="Event or agent not found")
    return GalileoEvent.model_validate(updated_event)


@router.post("/agents/{agent_id}/intervene", response_model=InterventionResult)
async def intervene(agent_id: str) -> InterventionResult:
    agent = await db_get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.get("status") != "Negotiating":
        raise HTTPException(status_code=409, detail="Agent is not currently negotiating")
    result = await db_intervene_agent(agent_id)
    return InterventionResult.model_validate(result)


@router.get("/agents/{agent_id}/activity-stream")
async def activity_stream(agent_id: str, request: Request) -> EventSourceResponse:
    return await _sse_stream(request, agent_id, payload_key="activityStream", event_name="activity")


@router.get("/agents/{agent_id}/transcript")
async def transcript_stream(agent_id: str, request: Request) -> EventSourceResponse:
    return await _sse_stream(request, agent_id, payload_key="transcript", event_name="message")


@router.post("/market/pricing", response_model=MarketPricingResult)
async def get_market_pricing(payload: MarketPricingRequest) -> MarketPricingResult:
    context = await _gather_pricing_context(payload)
    ai_result = await _ai_market_pricing(payload, context)
    if ai_result:
        return ai_result
    # Fallback
    return _static_market_pricing(payload)


async def _gather_pricing_context(payload: MarketPricingRequest) -> str:
    """Pull historic rates and past deals around the event date range."""
    from app.core.database import DB_PATH
    import aiosqlite

    # Parse month range from the event dates
    try:
        start_dt = datetime.fromisoformat(payload.startDate)
        end_dt = datetime.fromisoformat(payload.endDate)
    except ValueError:
        start_dt = end_dt = datetime.now()

    month_lo = max(start_dt.month - 2, 1)
    month_hi = min(start_dt.month + 2, 12)

    lines: list[str] = []
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    try:
        # Historic pricing within +/- 2 months of event
        hp_rows = await conn.execute_fetchall(
            """
            SELECT hotel, location, month, year, price_per_night
            FROM historic_pricing
            WHERE month BETWEEN ? AND ?
            ORDER BY year DESC, month DESC
            LIMIT 40
            """,
            (month_lo, month_hi),
        )
        if hp_rows:
            lines.append("## Historic Hotel Pricing (around event months)")
            for r in hp_rows:
                lines.append(
                    f"- {r['hotel']} | {r['location']} | {r['month']}/{r['year']} | ${r['price_per_night']}/night"
                )

        # Past completed negotiations with dates near the event window
        neg_rows = await conn.execute_fetchall(
            """
            SELECT e.name, e.location, e.start_date, e.end_date, e.attendees,
                   a.company_name, a.market_price, a.current_price
            FROM galileo_events e
            JOIN galileo_agents a ON a.event_id = e.id
            WHERE e.status = 'Completed' AND a.is_accepted = 1
            ORDER BY e.start_date DESC
            LIMIT 20
            """,
        )
        if neg_rows:
            lines.append("\n## Past Accepted Deals")
            for r in neg_rows:
                lines.append(
                    f"- {r['company_name']} | {r['location']} | {r['start_date']} to {r['end_date']} | "
                    f"{r['attendees']} attendees | market ${r['market_price']}/night | "
                    f"accepted ${r['current_price']}/night"
                )
    finally:
        await conn.close()

    return "\n".join(lines) if lines else "No historical data available."


async def _ai_market_pricing(
    payload: MarketPricingRequest, context: str
) -> MarketPricingResult | None:
    from app.core.shared_clients import get_openai_client

    duration_days = _days_between(payload.startDate, payload.endDate)

    prompt = f"""You are an expert corporate travel procurement analyst. Based on the historical
pricing data and past negotiation results below, estimate the current market rate and
the predicted negotiated win price for this event.

EVENT DETAILS:
- Service: {payload.service}
- Location: {payload.location}
- Dates: {payload.startDate} to {payload.endDate} ({duration_days} nights)
- Attendees: {payload.attendees}

{context}

Return EXACTLY a JSON object with these fields:
- "marketPrice": float (estimated market rate per night based on historical data for this location/season)
- "predictedWinPrice": float (realistic negotiated rate Galileo can achieve, based on past deal discounts)

Use the historical pricing and past negotiation discounts to inform realistic numbers.
Look at seasonal trends around the event months and typical discount percentages from
past accepted deals.

Return ONLY the JSON object, no markdown fencing, no extra text."""

    try:
        client = get_openai_client()
        resp = await client.chat.completions.create(
            model="o4-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        parsed = json.loads(raw)
        return MarketPricingResult.model_validate({
            "service": payload.service,
            "hotel": {
                "marketPrice": parsed["marketPrice"],
                "predictedWinPrice": parsed["predictedWinPrice"],
                "unit": "per night",
            },
        })
    except Exception as exc:
        logger.error("AI market pricing failed: %s", exc)
        return None


def _static_market_pricing(payload: MarketPricingRequest) -> MarketPricingResult:
    duration_days = _days_between(payload.startDate, payload.endDate)
    hotel_market, _airline_market = _pricing_baseline(payload.location, payload.attendees, duration_days)
    return MarketPricingResult.model_validate({
        "service": "Hotel",
        "hotel": {
            "marketPrice": hotel_market,
            "predictedWinPrice": round(hotel_market * 0.9, 2),
            "unit": "per night",
        },
    })


@router.post("/negotiations/launch", response_model=GalileoEvent)
async def launch_negotiations(payload: LaunchNegotiationRequest) -> GalileoEvent:
    try:
        created = await db_create_event_with_agents(payload)
    except TypeError:
        created = await db_create_event_with_agents(payload.model_dump())
    if not created:
        raise HTTPException(status_code=400, detail="Unable to launch negotiations")

    event = GalileoEvent.model_validate(created)

    # Trigger Twilio call for the agent marked as Negotiating
    for agent in event.agents:
        if agent.status == "Negotiating":
            asyncio.create_task(_trigger_twilio_call(agent, event))
            break

    return event


async def _trigger_twilio_call(agent: Agent, event: GalileoEvent) -> None:
    """Launch a voice campaign using the same API path as launch_voice_campaign.py."""
    import httpx

    settings = get_settings()
    base_url = settings.base_url.rstrip("/")
    campaign_id = f"galileo-{event.id}"
    destination_number = "+12609998910"

    target = {
        "hotel_id": agent.companyId,
        "phone_number": destination_number,
        "check_in": event.startDate,
        "check_out": event.endDate,
        "room_type": "standard",
        "target_rate": agent.idealPrice,
        "max_rate": agent.ceilingPrice,
        "priority_score": 1.0,
        "market_context": {
            "hotel_name": agent.companyName,
            "location": event.location,
            "market": event.location,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            store_resp = await client.put(
                f"{base_url}/api/campaigns/{campaign_id}/targets",
                json={"targets": [target]},
            )
            store_resp.raise_for_status()

            start_resp = await client.post(
                f"{base_url}/api/campaigns/{campaign_id}/start",
            )
            start_resp.raise_for_status()

        logger.info("Launched voice campaign %s for negotiating agent %s", campaign_id, agent.id)
    except Exception as exc:
        logger.error("Failed to launch voice campaign for agent %s: %s", agent.id, exc)


@router.post("/market/event-window", response_model=list[EventWindowResult])
async def find_event_window(payload: EventWindowRequest) -> list[EventWindowResult]:
    context = await _gather_event_window_context(payload)
    ai_windows = await _ai_event_window(payload, context)
    if ai_windows:
        return ai_windows
    # Fallback if AI fails
    return _static_event_window(payload)


async def _gather_event_window_context(payload: EventWindowRequest) -> str:
    """Pull past negotiations and historic rates from DB to feed the AI."""
    from app.core.database import DB_PATH
    import aiosqlite

    lines: list[str] = []
    conn = await aiosqlite.connect(str(DB_PATH))
    conn.row_factory = aiosqlite.Row
    try:
        # Past completed negotiations near this location
        rows = await conn.execute_fetchall(
            """
            SELECT e.name, e.location, e.start_date, e.end_date, e.attendees,
                   a.company_name, a.ideal_price, a.ceiling_price, a.market_price, a.current_price
            FROM galileo_events e
            JOIN galileo_agents a ON a.event_id = e.id
            WHERE e.status = 'Completed' AND a.is_accepted = 1
            ORDER BY e.start_date DESC
            LIMIT 20
            """,
        )
        if rows:
            lines.append("## Past Completed Negotiations (accepted deals)")
            for r in rows:
                lines.append(
                    f"- {r['company_name']} | {r['location']} | {r['start_date']} to {r['end_date']} | "
                    f"{r['attendees']} attendees | market ${r['market_price']}/night | "
                    f"accepted ${r['current_price']}/night"
                )

        # Historic pricing data
        hp_rows = await conn.execute_fetchall(
            """
            SELECT hotel, location, month, year, price_per_night
            FROM historic_pricing
            ORDER BY year DESC, month DESC
            LIMIT 40
            """,
        )
        if hp_rows:
            lines.append("\n## Historic Hotel Pricing")
            for r in hp_rows:
                lines.append(
                    f"- {r['hotel']} | {r['location']} | {r['month']}/{r['year']} | ${r['price_per_night']}/night"
                )
    finally:
        await conn.close()

    return "\n".join(lines) if lines else "No historical data available."


async def _ai_event_window(
    payload: EventWindowRequest, context: str
) -> list[EventWindowResult] | None:
    """Use an AI reasoning model to produce event window recommendations."""
    from app.core.shared_clients import get_openai_client

    year = _extract_year(payload.preferredTiming)
    start_month = _quarter_start_month(payload.preferredTiming)

    prompt = f"""You are an expert corporate travel procurement analyst. Based on the historical
negotiation data and hotel pricing below, recommend exactly 3 event date windows for the
upcoming event.

EVENT DETAILS:
- Location: {payload.location}
- Event type: {payload.eventType}
- Preferred timing: {payload.preferredTiming}
- Attendees: {payload.attendees}
- Nights: {payload.nights}
- Additional details: {payload.eventDetails or "None"}
- Reference year: {year}, reference quarter start month: {start_month}

{context}

Return EXACTLY a JSON array with 3 objects. Each object must have these fields:
- "label": one of "Best Overall", "Backup Window", "Budget Window"
- "startDate": ISO date string (YYYY-MM-DD)
- "endDate": ISO date string (YYYY-MM-DD), must be startDate + {payload.nights} days
- "explanation": 1-2 sentence reasoning referencing the data
- "hotel": object with "marketCost" (float), "negotiatedPrice" (float), "savings" (float)
  - marketCost = estimated market rate per night * {payload.nights} nights * {payload.attendees} attendees
  - negotiatedPrice = what Galileo can realistically negotiate based on past deals
  - savings = marketCost - negotiatedPrice
- "negotiationConfidence": float 0.0-1.0

Use the historical data to inform realistic pricing. Base market rates and negotiated rates on
actual past deals and seasonal pricing trends you can see in the data. The dates should be
within or near the preferred timing quarter of {year}.

Return ONLY the JSON array, no markdown fencing, no extra text."""

    try:
        client = get_openai_client()
        resp = await client.chat.completions.create(
            model="o4-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fencing if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        parsed = json.loads(raw)
        return [EventWindowResult.model_validate(w) for w in parsed]
    except Exception as exc:
        logger.error("AI event window failed: %s", exc)
        return None


def _static_event_window(payload: EventWindowRequest) -> list[EventWindowResult]:
    """Deterministic fallback if AI is unavailable."""
    year = _extract_year(payload.preferredTiming)
    start_month = _quarter_start_month(payload.preferredTiming)
    base_start = date(year, start_month, 1)

    windows = [
        ("Best Overall", -28, 86.0, 0.90, "Best blend of supplier flexibility and attendee convenience."),
        ("Backup Window", 14, 81.0, 0.94, "Slightly higher demand but strong negotiating leverage."),
        ("Budget Window", 42, 75.0, 0.84, "Lowest expected rates with a moderate concession risk."),
    ]

    results: list[EventWindowResult] = []
    for label, shift_days, confidence, discount, explanation in windows:
        start = base_start + timedelta(days=shift_days)
        end = start + timedelta(days=max(payload.nights, 1))
        hotel_market, _airline_market = _pricing_baseline(
            payload.location,
            payload.attendees,
            max(payload.nights, 1),
        )
        room_nights = max(payload.nights, 1) * max(payload.attendees, 1)
        hotel_market_cost = round(hotel_market * room_nights, 2)
        hotel_negotiated = round(hotel_market_cost * discount, 2)

        event_window = {
            "label": label,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "explanation": explanation,
            "hotel": {
                "marketCost": hotel_market_cost,
                "negotiatedPrice": hotel_negotiated,
                "savings": round(hotel_market_cost - hotel_negotiated, 2),
            },
            "negotiationConfidence": round(confidence, 2),
        }
        results.append(EventWindowResult.model_validate(event_window))

    return results

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.galileo.database import (
    accept_offer as db_accept_offer,
    create_event_with_agents as db_create_event_with_agents,
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
    duration_days = _days_between(payload.startDate, payload.endDate)
    hotel_market, airline_market = _pricing_baseline(payload.location, payload.attendees, duration_days)

    hotel = {
        "marketPrice": hotel_market,
        "predictedWinPrice": round(hotel_market * 0.9, 2),
        "unit": "per night",
    }
    airline = {
        "marketPrice": airline_market,
        "predictedWinPrice": round(airline_market * 0.88, 2),
        "unit": "per seat",
    }

    service = payload.service.value
    normalized = service.strip().lower()
    return MarketPricingResult.model_validate(
        {
            "service": service,
            "hotel": hotel if normalized in {"hotel", "both"} else None,
            "airline": airline if normalized in {"airline", "both"} else None,
        }
    )


@router.post("/negotiations/launch", response_model=GalileoEvent)
async def launch_negotiations(payload: LaunchNegotiationRequest) -> GalileoEvent:
    try:
        created = await db_create_event_with_agents(payload)
    except TypeError:
        created = await db_create_event_with_agents(payload.model_dump())
    if not created:
        raise HTTPException(status_code=400, detail="Unable to launch negotiations")
    return GalileoEvent.model_validate(created)


@router.post("/market/event-window", response_model=list[EventWindowResult])
async def find_event_window(payload: EventWindowRequest) -> list[EventWindowResult]:
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
        hotel_market, airline_market = _pricing_baseline(
            payload.location,
            payload.attendees,
            max(payload.nights, 1),
        )
        room_nights = max(payload.nights, 1) * max(payload.attendees, 1)
        seats = max(payload.attendees, 1)
        hotel_market_cost = round(hotel_market * room_nights, 2)
        airline_market_cost = round(airline_market * seats, 2)
        hotel_negotiated = round(hotel_market_cost * discount, 2)
        airline_negotiated = round(airline_market_cost * (discount - 0.02), 2)

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
            "airline": {
                "marketCost": airline_market_cost,
                "negotiatedPrice": airline_negotiated,
                "savings": round(airline_market_cost - airline_negotiated, 2),
            },
            "negotiationConfidence": round(confidence, 2),
        }
        results.append(EventWindowResult.model_validate(event_window))

    return results

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.hotel.schemas import HotelTarget
from app.orchestration.campaign_graph import build_campaign_graph, make_initial_campaign_state

router = APIRouter()
logger = logging.getLogger(__name__)

# campaign_id -> asyncio.Task
_running_campaigns: dict[str, asyncio.Task] = {}
_campaign_targets: dict[str, list[HotelTarget]] = {}
_campaign_summaries: dict[str, dict] = {}


def get_campaign_targets_local(campaign_id: str) -> list[HotelTarget]:
    """Direct access to stored targets without HTTP round-trip."""
    return list(_campaign_targets.get(campaign_id, []))


class StartCampaignRequest(BaseModel):
    campaign_id: str


class CampaignTargetsRequest(BaseModel):
    targets: list[HotelTarget]


@router.put("/campaigns/{campaign_id}/targets")
async def upsert_campaign_targets(campaign_id: str, request: CampaignTargetsRequest) -> dict:
    _campaign_targets[campaign_id] = request.targets
    logger.info("Campaign %s stored %d targets", campaign_id, len(request.targets))
    return {"campaign_id": campaign_id, "target_count": len(request.targets), "status": "stored"}


@router.get("/campaigns/{campaign_id}/targets")
async def get_campaign_targets(campaign_id: str) -> list[dict]:
    return [target.model_dump() for target in _campaign_targets.get(campaign_id, [])]


@router.post("/campaigns/{campaign_id}/summary")
async def store_campaign_summary(campaign_id: str, payload: dict) -> dict:
    _campaign_summaries[campaign_id] = payload
    logger.info("Campaign %s summary stored", campaign_id)
    return {"campaign_id": campaign_id, "status": "stored"}


@router.get("/campaigns/{campaign_id}/summary")
async def get_campaign_summary(campaign_id: str) -> dict:
    summary = _campaign_summaries.get(campaign_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Campaign summary not found")
    return summary


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: str) -> dict:
    if campaign_id in _running_campaigns:
        task = _running_campaigns[campaign_id]
        if not task.done():
            raise HTTPException(status_code=409, detail="Campaign already running")
    if not _campaign_targets.get(campaign_id):
        raise HTTPException(status_code=400, detail="Campaign has no targets")

    graph = build_campaign_graph()
    initial_state = make_initial_campaign_state(campaign_id)

    task = asyncio.create_task(
        graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": campaign_id}},
        ),
        name=f"campaign-{campaign_id}",
    )
    _running_campaigns[campaign_id] = task
    logger.info("Campaign %s started", campaign_id)
    return {"campaign_id": campaign_id, "status": "started"}


@router.get("/campaigns/{campaign_id}/status")
async def get_campaign_status(campaign_id: str) -> dict:
    task = _running_campaigns.get(campaign_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if task.done():
        exc = task.exception()
        return {
            "campaign_id": campaign_id,
            "status": "failed" if exc else "completed",
            "error": str(exc) if exc else None,
        }
    return {"campaign_id": campaign_id, "status": "running"}


@router.delete("/campaigns/{campaign_id}")
async def cancel_campaign(campaign_id: str) -> dict:
    task = _running_campaigns.get(campaign_id)
    if task is None or task.done():
        raise HTTPException(status_code=404, detail="No active campaign found")
    task.cancel()
    logger.info("Campaign %s cancelled", campaign_id)
    return {"campaign_id": campaign_id, "status": "cancelled"}

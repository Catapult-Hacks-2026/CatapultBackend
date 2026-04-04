from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.orchestration.campaign_graph import build_campaign_graph, make_initial_campaign_state

router = APIRouter()
logger = logging.getLogger(__name__)

# campaign_id -> asyncio.Task
_running_campaigns: dict[str, asyncio.Task] = {}


class StartCampaignRequest(BaseModel):
    campaign_id: str


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(campaign_id: str) -> dict:
    if campaign_id in _running_campaigns:
        task = _running_campaigns[campaign_id]
        if not task.done():
            raise HTTPException(status_code=409, detail="Campaign already running")

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

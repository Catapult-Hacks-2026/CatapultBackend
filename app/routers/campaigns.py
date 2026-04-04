from fastapi import APIRouter, BackgroundTasks, WebSocket, WebSocketDisconnect

from app.models.schemas import CampaignResponse, CampaignSummary, CreateCampaignRequest
from app.services.campaign import (
    create_campaign,
    emit_campaign_summary,
    get_campaign,
    list_campaign_jobs,
    list_campaigns,
    pause_campaign,
    resume_campaign,
    start_campaign,
)
from app.services.events import subscribe_worker_events

router = APIRouter()


@router.post("/", response_model=CampaignResponse)
async def create_campaign_endpoint(
    request: CreateCampaignRequest,
    background_tasks: BackgroundTasks,
) -> CampaignResponse:
    campaign = create_campaign(request)
    if request.auto_start:
        background_tasks.add_task(start_campaign, campaign["id"])
    return CampaignResponse.model_validate(campaign)


@router.get("/", response_model=list[CampaignResponse])
def list_campaigns_endpoint() -> list[CampaignResponse]:
    return [CampaignResponse.model_validate(item) for item in list_campaigns()]


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign_endpoint(campaign_id: str) -> CampaignResponse:
    return CampaignResponse.model_validate(get_campaign(campaign_id))


@router.get("/{campaign_id}/jobs")
def list_campaign_jobs_endpoint(campaign_id: str) -> list[dict]:
    return list_campaign_jobs(campaign_id)


@router.post("/{campaign_id}/start", response_model=CampaignResponse)
async def start_campaign_endpoint(campaign_id: str) -> CampaignResponse:
    return CampaignResponse.model_validate(await start_campaign(campaign_id))


@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
def pause_campaign_endpoint(campaign_id: str) -> CampaignResponse:
    return CampaignResponse.model_validate(pause_campaign(campaign_id))


@router.post("/{campaign_id}/resume", response_model=CampaignResponse)
async def resume_campaign_endpoint(campaign_id: str) -> CampaignResponse:
    return CampaignResponse.model_validate(await resume_campaign(campaign_id))


@router.get("/{campaign_id}/summary", response_model=CampaignSummary)
def campaign_summary_endpoint(campaign_id: str) -> CampaignSummary:
    return CampaignSummary.model_validate(emit_campaign_summary(campaign_id))


@router.websocket("/{campaign_id}/ws")
async def campaign_ws(websocket: WebSocket, campaign_id: str) -> None:
    await websocket.accept()
    try:
        async for event in subscribe_worker_events(campaign_id):
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return

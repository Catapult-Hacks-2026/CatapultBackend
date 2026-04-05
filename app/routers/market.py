from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()

_quotes_by_hotel: dict[str, dict] = {}


class QuoteUpsertRequest(BaseModel):
    session_id: str
    hotel_id: str
    nightly_rate: float
    total_rate: float
    inclusions: dict[str, bool] = Field(default_factory=dict)
    cancellation_policy: str = ""
    rate_type: str = ""
    fees: float = 0.0


@router.get("/market/state")
async def get_market_state() -> dict:
    return {
        "confirmed_quotes": dict(_quotes_by_hotel),
    }


@router.get("/market/signals")
async def get_market_signals() -> dict:
    return {
        hotel_id: {
            "has_quote": True,
            "last_rate": quote["nightly_rate"],
            "rate_type": quote.get("rate_type", ""),
            "updated_at": quote["updated_at"],
        }
        for hotel_id, quote in _quotes_by_hotel.items()
    }


@router.post("/quotes/")
async def upsert_quote(request: QuoteUpsertRequest) -> dict:
    _quotes_by_hotel[request.hotel_id] = {
        "session_id": request.session_id,
        "hotel_id": request.hotel_id,
        "nightly_rate": request.nightly_rate,
        "total_rate": request.total_rate,
        "inclusions": request.inclusions,
        "cancellation_policy": request.cancellation_policy,
        "rate_type": request.rate_type,
        "fees": request.fees,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"status": "stored", "hotel_id": request.hotel_id}

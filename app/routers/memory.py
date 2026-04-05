from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

_features_by_hotel: dict[str, list[dict]] = {}


@router.get("/memory/priors/{hotel_id}")
async def get_memory_priors(hotel_id: str) -> dict:
    return {
        "profile": {
            "last_call_date": None,
            "total_calls": 0,
            "success_rate": 0.0,
            "avg_negotiated_discount": 0.0,
            "best_rate": None,
            "worst_rate": None,
            "common_objections": [],
            "escalation_success_rate": 0.0,
        },
        "features": list(_features_by_hotel.get(hotel_id, [])),
    }


@router.post("/memory/features/")
async def store_memory_features(payload: list[dict]) -> dict:
    stored = 0
    for feature in payload:
        hotel_id = feature.get("hotel_id")
        if not isinstance(hotel_id, str) or not hotel_id:
            continue
        _features_by_hotel.setdefault(hotel_id, []).append(feature)
        stored += 1
    return {"status": "stored", "count": stored}

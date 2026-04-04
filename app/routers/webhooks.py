from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.database import get_db
from app.models.enums import NegotiationStatus, Strategy
from app.models.schemas import BuyerConfig, CreateNegotiationRequest, InboundVendorMessage
from app.services.negotiation import create_negotiation_record, process_vendor_input
from app.services.research import generate_negotiation_brief

router = APIRouter()


def _default_buyer_config() -> BuyerConfig:
    return BuyerConfig(
        target_unit_price=85.0,
        max_unit_price=100.0,
        target_shipping_cost=0.0,
        max_shipping_cost=200.0,
        preferred_payment_terms=60,
        min_payment_terms=30,
        preferred_delivery_days=14,
        max_delivery_days=30,
        quantity=1000,
    )


@router.post("/vendor")
async def inbound_vendor_message(request: InboundVendorMessage) -> dict:
    from app.main import manager

    result = process_vendor_input(request.negotiation_id, request.message, request.offer)
    await manager.broadcast(request.negotiation_id, {"type": "negotiation_update", **result})
    return result


@router.post("/simulate")
async def simulate_vendor_message(payload: dict, background_tasks: BackgroundTasks) -> dict:
    from app.main import manager

    vendor_name = payload.get("vendor_name")
    message = payload.get("message")
    if not vendor_name or not message:
        raise HTTPException(status_code=400, detail="vendor_name and message are required")

    conn = get_db()
    negotiation = conn.execute(
        """
        SELECT * FROM negotiations
        WHERE vendor_name = ? AND status NOT IN (?, ?)
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (vendor_name, NegotiationStatus.ACCEPTED.value, NegotiationStatus.ESCALATED.value),
    ).fetchone()
    conn.close()

    if negotiation is None:
        request = CreateNegotiationRequest(
            vendor_name=vendor_name,
            strategy=Strategy(payload.get("strategy", Strategy.BALANCED.value)),
            product_category=payload.get("product_category", "general"),
            config=BuyerConfig.model_validate(payload.get("config", _default_buyer_config().model_dump())),
        )
        row = create_negotiation_record(
            vendor_name=request.vendor_name,
            strategy=request.strategy.value,
            config=request.config,
            product_category=request.product_category,
        )
        background_tasks.add_task(generate_negotiation_brief, row["id"])
        negotiation_id = row["id"]
    else:
        negotiation_id = negotiation["id"]

    result = process_vendor_input(negotiation_id, message, None)
    await manager.broadcast(negotiation_id, {"type": "negotiation_update", **result})
    return {"negotiation_id": negotiation_id, **result}

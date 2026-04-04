import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core.database import get_db
from app.models.enums import MessageRole, NegotiationStatus, Strategy
from app.models.schemas import (
    BatchNegotiationRequest,
    BatchNegotiationResponse,
    BatchNegotiationResult,
    BuyerConfig,
    CreateNegotiationRequest,
    NegotiationResponse,
    VendorOffer,
)
from app.services.adversarial import run_adversarial_negotiation
from app.services.negotiation import create_negotiation_record
from app.services.research import generate_negotiation_brief
from app.services.scoring import score_offer, suggest_pivot
from app.services.voice import initiate_call

router = APIRouter()

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_MARKET_CSVS = [
    "San Francisco Hospitality Market Data - Chicago Historic Pricing.csv",
    "San Francisco Hospitality Market Data - Chicago Past Negotiations.csv",
]


class ImportMarketDataRequest(BaseModel):
    filenames: list[str] = _DEFAULT_MARKET_CSVS.copy()
    truncate_existing: bool = False


def _parse_money(value: str) -> float:
    return float(value.replace("$", "").replace(",", "").strip())


def _parse_year(value: str) -> int:
    return int(value.replace("$", "").replace(",", "").strip())


def _parse_month(value: str) -> int:
    return datetime.strptime(value.strip(), "%B").month


def _table_columns(conn, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _import_historic_pricing(conn, csv_path: Path) -> int:
    inserted = 0
    columns = _table_columns(conn, "historic_pricing")
    use_legacy_date = "date" in columns and "month" not in columns and "year" not in columns
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if use_legacy_date:
                conn.execute(
                    """
                    INSERT INTO historic_pricing (hotel, location, date, price_per_night)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        row["Hotel Chain"].strip(),
                        row["Location"].strip(),
                        f"{row['Month'].strip()} {_parse_year(row['Year'])}",
                        _parse_money(row["$/night"]),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO historic_pricing (hotel, location, month, year, price_per_night)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["Hotel Chain"].strip(),
                        row["Location"].strip(),
                        _parse_month(row["Month"]),
                        _parse_year(row["Year"]),
                        _parse_money(row["$/night"]),
                    ),
                )
            inserted += 1
    return inserted


def _import_past_negotiations(conn, csv_path: Path) -> int:
    inserted = 0
    columns = _table_columns(conn, "past_negotiations")
    use_legacy_date = "date" in columns and "month" not in columns and "year" not in columns
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            date_value = datetime.strptime(row["Date"].strip(), "%Y-%m-%d")
            if use_legacy_date:
                conn.execute(
                    """
                    INSERT INTO past_negotiations (
                        hotel, location, date, starting_price, negotiation_price, proposed_price
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["Hotel Chain"].strip(),
                        row["Location"].strip(),
                        row["Date"].strip(),
                        _parse_money(row["Starting Rate ($/night)"]),
                        _parse_money(row["Negotiated Rate ($/night)"]),
                        _parse_money(row["Initial Proposed Rate ($/night)"]),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO past_negotiations (
                        hotel, location, month, year, starting_price, negotiation_price, proposed_price
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["Hotel Chain"].strip(),
                        row["Location"].strip(),
                        date_value.month,
                        date_value.year,
                        _parse_money(row["Starting Rate ($/night)"]),
                        _parse_money(row["Negotiated Rate ($/night)"]),
                        _parse_money(row["Initial Proposed Rate ($/night)"]),
                    ),
                )
            inserted += 1
    return inserted


def _import_market_csvs(payload: ImportMarketDataRequest) -> dict:
    known_importers = {
        "San Francisco Hospitality Market Data - Chicago Historic Pricing.csv": ("historic_pricing", _import_historic_pricing),
        "San Francisco Hospitality Market Data - Chicago Past Negotiations.csv": ("past_negotiations", _import_past_negotiations),
    }
    conn = get_db()
    counts: dict[str, int] = {}
    try:
        if payload.truncate_existing:
            touched_tables = {known_importers[name][0] for name in payload.filenames if name in known_importers}
            for table_name in touched_tables:
                conn.execute(f"DELETE FROM {table_name}")

        for filename in payload.filenames:
            importer_info = known_importers.get(filename)
            if importer_info is None:
                raise HTTPException(status_code=400, detail=f"Unsupported CSV file: {filename}")
            csv_path = _PROJECT_ROOT / filename
            if not csv_path.exists():
                raise HTTPException(status_code=404, detail=f"CSV file not found: {filename}")
            table_name, importer = importer_info
            counts[table_name] = counts.get(table_name, 0) + importer(conn, csv_path)

        conn.commit()
        return {"imported": counts, "filenames": payload.filenames, "truncate_existing": payload.truncate_existing}
    finally:
        conn.close()


def _parse_offer(payload: str | None) -> VendorOffer | None:
    if not payload:
        return None
    return VendorOffer.model_validate_json(payload)


def _serialize_negotiation(row) -> NegotiationResponse:
    offer = _parse_offer(row["current_offer"])
    return NegotiationResponse(
        id=row["id"],
        vendor_name=row["vendor_name"],
        product_category=row["product_category"],
        status=row["status"],
        strategy=row["strategy"],
        round_number=row["round_number"],
        utility_score=row["utility_score"],
        current_offer=offer,
    )


@router.post("/", response_model=NegotiationResponse)
def create_negotiation(
    request: CreateNegotiationRequest,
    background_tasks: BackgroundTasks,
) -> NegotiationResponse:
    row = create_negotiation_record(
        vendor_name=request.vendor_name,
        strategy=request.strategy.value,
        config=request.config,
        product_category=request.product_category,
    )
    background_tasks.add_task(generate_negotiation_brief, row["id"])
    return _serialize_negotiation(row)


@router.post("/batch", response_model=BatchNegotiationResponse)
async def create_batch_negotiations(
    request: BatchNegotiationRequest,
    background_tasks: BackgroundTasks,
) -> BatchNegotiationResponse:
    if not request.vendors:
        raise HTTPException(status_code=400, detail="At least one vendor is required")

    async def _create_item(vendor_config) -> tuple:
        row = await asyncio.to_thread(
            create_negotiation_record,
            vendor_config.vendor_name,
            vendor_config.strategy.value,
            request.config,
            request.product_category,
            10,
        )
        call_status = None
        if request.auto_call and vendor_config.vendor_phone_number:
            call_status = await asyncio.to_thread(
                initiate_call,
                row["id"],
                vendor_config.vendor_phone_number,
            )
        return row, call_status

    created = await asyncio.gather(*[_create_item(vendor) for vendor in request.vendors])
    results = []
    for row, call_status in created:
        background_tasks.add_task(generate_negotiation_brief, row["id"])
        results.append(
            BatchNegotiationResult(
                negotiation=_serialize_negotiation(row),
                call_status=call_status,
            )
        )
    return BatchNegotiationResponse(items=results)


@router.post("/import-market-data")
def import_market_data(payload: ImportMarketDataRequest) -> dict:
    return _import_market_csvs(payload)


@router.get("/", response_model=list[NegotiationResponse])
def list_negotiations() -> list[NegotiationResponse]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM negotiations ORDER BY updated_at DESC, created_at DESC"
    ).fetchall()
    conn.close()
    return [_serialize_negotiation(row) for row in rows]


@router.get("/{negotiation_id}")
def get_negotiation(negotiation_id: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM negotiations WHERE id = ?", (negotiation_id,)).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Negotiation not found")
    messages = conn.execute(
        "SELECT * FROM messages WHERE negotiation_id = ? ORDER BY created_at, id",
        (negotiation_id,),
    ).fetchall()
    conn.close()
    return {
        "negotiation": _serialize_negotiation(row).model_dump(),
        "research_brief": json.loads(row["research_brief"]) if row["research_brief"] else None,
        "config": BuyerConfig.model_validate_json(row["config"]).model_dump(),
        "messages": [
            {
                "id": message["id"],
                "role": message["role"],
                "content": message["content"],
                "structured_data": json.loads(message["structured_data"]) if message["structured_data"] else None,
                "utility_score": message["utility_score"],
                "rag_context": json.loads(message["rag_context"]) if message["rag_context"] else None,
                "guardrail_log": json.loads(message["guardrail_log"]) if message["guardrail_log"] else None,
                "created_at": message["created_at"],
            }
            for message in messages
        ],
    }


@router.get("/{negotiation_id}/messages")
def get_messages(negotiation_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM messages WHERE negotiation_id = ? ORDER BY created_at, id",
        (negotiation_id,),
    ).fetchall()
    conn.close()
    return [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "structured_data": json.loads(row["structured_data"]) if row["structured_data"] else None,
            "utility_score": row["utility_score"],
            "rag_context": json.loads(row["rag_context"]) if row["rag_context"] else None,
            "guardrail_log": json.loads(row["guardrail_log"]) if row["guardrail_log"] else None,
            "created_at": row["created_at"],
        }
        for row in rows
    ]


@router.get("/{negotiation_id}/scoring")
def get_scoring(negotiation_id: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM negotiations WHERE id = ?", (negotiation_id,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Negotiation not found")

    current_offer = _parse_offer(row["current_offer"])
    config = BuyerConfig.model_validate_json(row["config"])
    if current_offer is None:
        return {"scoring_breakdown": None, "pivot_suggestions": {}, "status": row["status"]}

    breakdown = score_offer(current_offer, config)
    return {
        "scoring_breakdown": breakdown.model_dump(),
        "pivot_suggestions": suggest_pivot(current_offer, config, breakdown),
        "status": row["status"],
    }


@router.patch("/{negotiation_id}")
def update_negotiation(negotiation_id: str, payload: dict) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM negotiations WHERE id = ?", (negotiation_id,)).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Negotiation not found")

    strategy = payload.get("strategy", row["strategy"])
    config_payload = payload.get("config")
    config = BuyerConfig.model_validate(config_payload) if config_payload else BuyerConfig.model_validate_json(row["config"])
    if isinstance(strategy, Strategy):
        strategy = strategy.value

    conn.execute(
        "UPDATE negotiations SET strategy = ?, config = ?, updated_at = ? WHERE id = ?",
        (strategy, config.model_dump_json(), datetime.now(UTC).isoformat(), negotiation_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM negotiations WHERE id = ?", (negotiation_id,)).fetchone()
    conn.close()
    return _serialize_negotiation(updated).model_dump()


@router.post("/{negotiation_id}/approve")
def approve_negotiation(negotiation_id: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM negotiations WHERE id = ?", (negotiation_id,)).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Negotiation not found")
    conn.execute(
        "UPDATE negotiations SET status = ?, updated_at = ? WHERE id = ?",
        (NegotiationStatus.ACCEPTED.value, datetime.now(UTC).isoformat(), negotiation_id),
    )
    conn.execute(
        """
        INSERT INTO messages (negotiation_id, role, content, structured_data, utility_score)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            negotiation_id,
            MessageRole.SYSTEM.value,
            "Human approved the current deal.",
            row["current_offer"],
            row["utility_score"],
        ),
    )
    conn.commit()
    conn.close()
    return {"status": NegotiationStatus.ACCEPTED.value}


@router.post("/{negotiation_id}/escalate")
def escalate_negotiation(negotiation_id: str) -> dict:
    conn = get_db()
    exists = conn.execute("SELECT 1 FROM negotiations WHERE id = ?", (negotiation_id,)).fetchone()
    if exists is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Negotiation not found")
    conn.execute(
        "UPDATE negotiations SET status = ?, updated_at = ? WHERE id = ?",
        (NegotiationStatus.ESCALATED.value, datetime.now(UTC).isoformat(), negotiation_id),
    )
    conn.execute(
        """
        INSERT INTO messages (negotiation_id, role, content)
        VALUES (?, ?, ?)
        """,
        (negotiation_id, MessageRole.SYSTEM.value, "Human escalated this negotiation."),
    )
    conn.commit()
    conn.close()
    return {"status": NegotiationStatus.ESCALATED.value}


@router.websocket("/{negotiation_id}/ws")
async def negotiation_ws(negotiation_id: str, websocket: WebSocket) -> None:
    from app.main import manager

    await manager.connect(negotiation_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(negotiation_id, websocket)


@router.post("/adversarial")
def adversarial_demo(payload: dict) -> dict:
    buyer_config = BuyerConfig.model_validate(payload["buyer_config"])
    seller_config = payload["seller_config"]
    max_rounds = int(payload.get("max_rounds", 10))
    return {
        "rounds": run_adversarial_negotiation(
            buyer_config=buyer_config,
            seller_config=seller_config,
            max_rounds=max_rounds,
        )
    }

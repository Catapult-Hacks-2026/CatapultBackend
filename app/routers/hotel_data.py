from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class HistoricPricingIn(BaseModel):
    hotel: str
    location: str
    month: int
    year: int
    price_per_night: float


class HistoricPricingOut(HistoricPricingIn):
    id: int
    created_at: str


class PastNegotiationIn(BaseModel):
    hotel: str
    location: str
    month: int
    year: int
    starting_price: float
    negotiation_price: float
    proposed_price: float


class PastNegotiationOut(PastNegotiationIn):
    id: int
    created_at: str


# ---------------------------------------------------------------------------
# Historic Pricing endpoints
# ---------------------------------------------------------------------------

@router.post("/historic-pricing", response_model=HistoricPricingOut)
async def create_historic_pricing(item: HistoricPricingIn) -> dict:
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO historic_pricing (hotel, location, month, year, price_per_night)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item.hotel, item.location, item.month, item.year, item.price_per_night),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM historic_pricing WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/historic-pricing", response_model=list[HistoricPricingOut])
async def list_historic_pricing(
    hotel: str | None = Query(None),
    location: str | None = Query(None),
) -> list[dict]:
    conn = get_db()
    try:
        query = "SELECT * FROM historic_pricing WHERE 1=1"
        params: list = []
        if hotel:
            query += " AND hotel = ?"
            params.append(hotel)
        if location:
            query += " AND location = ?"
            params.append(location)
        query += " ORDER BY year DESC, month DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/historic-pricing/{item_id}", response_model=HistoricPricingOut)
async def get_historic_pricing(item_id: int) -> dict:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM historic_pricing WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Historic pricing record not found")
        return dict(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Past Negotiations endpoints
# ---------------------------------------------------------------------------

@router.post("/past-negotiations", response_model=PastNegotiationOut)
async def create_past_negotiation(item: PastNegotiationIn) -> dict:
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO past_negotiations (hotel, location, month, year, starting_price, negotiation_price, proposed_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (item.hotel, item.location, item.month, item.year, item.starting_price, item.negotiation_price, item.proposed_price),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM past_negotiations WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.get("/past-negotiations", response_model=list[PastNegotiationOut])
async def list_past_negotiations(
    hotel: str | None = Query(None),
    location: str | None = Query(None),
) -> list[dict]:
    conn = get_db()
    try:
        query = "SELECT * FROM past_negotiations WHERE 1=1"
        params: list = []
        if hotel:
            query += " AND hotel = ?"
            params.append(hotel)
        if location:
            query += " AND location = ?"
            params.append(location)
        query += " ORDER BY year DESC, month DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/past-negotiations/{item_id}", response_model=PastNegotiationOut)
async def get_past_negotiation(item_id: int) -> dict:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM past_negotiations WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Past negotiation record not found")
        return dict(row)
    finally:
        conn.close()

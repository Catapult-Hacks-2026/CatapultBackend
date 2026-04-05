"""Market-data pipeline: SQLite → Redis → negotiation brain context.

Reads historic pricing and past negotiation records from the database,
caches them in Redis sorted sets for low-latency lookups, and exposes
a retrieval function that builds a concise market brief for the LLM.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from app.core.database import get_db, get_redis

logger = logging.getLogger(__name__)

# Redis key layout
_PRICING_PREFIX = "market:pricing"       # sorted set per hotel, scored by YYYYMM
_DEALS_PREFIX = "market:deals"           # sorted set per hotel, scored by YYYYMM
_LOCATION_PRICING = "market:loc_pricing" # sorted set per location
_LOCATION_DEALS = "market:loc_deals"     # sorted set per location
_SYNC_SENTINEL = "market:_synced"

# ─── CSV seed ────────────────────────────────────────────────────────

_CSV_DIR = Path(__file__).resolve().parents[2]  # repo root

_PRICING_CSV = _CSV_DIR / "San Francisco Hospitality Market Data - Chicago Historic Pricing.csv"
_DEALS_CSV = _CSV_DIR / "San Francisco Hospitality Market Data - Chicago Past Negotiations.csv"


def _parse_dollar(val: str) -> float:
    """Parse '$825' or '$2,025' (the year column has commas) into a float."""
    return float(val.replace("$", "").replace(",", "").strip())


def seed_market_data() -> int:
    """Load CSV files into the historic_pricing and past_negotiations tables.

    Skips rows that already exist (idempotent).  Returns total rows inserted.
    """
    conn = get_db()
    inserted = 0

    if _PRICING_CSV.exists():
        with open(_PRICING_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                hotel = row["Hotel Chain"].strip()
                location = row["Location"].strip()
                year = int(_parse_dollar(row["Year"]))
                month_str = row["Month"].strip()
                month = _month_number(month_str)
                price = _parse_dollar(row["$/night"])

                exists = conn.execute(
                    "SELECT 1 FROM historic_pricing WHERE hotel=? AND location=? AND month=? AND year=?",
                    (hotel, location, month, year),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO historic_pricing (hotel, location, month, year, price_per_night) VALUES (?,?,?,?,?)",
                        (hotel, location, month, year, price),
                    )
                    inserted += 1

    if _DEALS_CSV.exists():
        with open(_DEALS_CSV, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                hotel = row["Hotel Chain"].strip()
                location = row["Location"].strip()
                date_str = row["Date"].strip()
                # date is YYYY-MM-DD
                parts = date_str.split("-")
                year = int(parts[0])
                month = int(parts[1])
                starting = _parse_dollar(row["Starting Rate ($/night)"])
                negotiated = _parse_dollar(row["Negotiated Rate ($/night)"])
                proposed = _parse_dollar(row["Initial Proposed Rate ($/night)"])

                exists = conn.execute(
                    "SELECT 1 FROM past_negotiations WHERE hotel=? AND location=? AND month=? AND year=?",
                    (hotel, location, month, year),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO past_negotiations (hotel, location, month, year, starting_price, negotiation_price, proposed_price) VALUES (?,?,?,?,?,?,?)",
                        (hotel, location, month, year, starting, negotiated, proposed),
                    )
                    inserted += 1

    conn.commit()
    conn.close()
    if inserted:
        logger.info("seed_market_data: inserted %d rows", inserted)
    return inserted


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _month_number(name: str) -> int:
    return _MONTHS.get(name.strip().lower(), 1)


def _is_near_month(record_month: int, target_month: int, window: int = 2) -> bool:
    """Check if record_month is within +/-window of target_month, wrapping Dec/Jan."""
    diff = abs(record_month - target_month)
    return min(diff, 12 - diff) <= window


# ─── SQLite → Redis sync ────────────────────────────────────────────

def sync_market_data_to_redis(force: bool = False) -> int:
    """Read all market data from SQLite and push into Redis sorted sets.

    Each record is stored as a JSON blob in a hash, and indexed in sorted
    sets by hotel name and by location for flexible retrieval.

    Returns number of records synced.
    """
    r = get_redis()
    if not force and r.exists(_SYNC_SENTINEL):
        return 0

    conn = get_db()
    synced = 0
    pipe = r.pipeline()

    # Historic pricing
    rows = conn.execute("SELECT hotel, location, month, year, price_per_night FROM historic_pricing").fetchall()
    for row in rows:
        hotel, location, month, year, price = row["hotel"], row["location"], row["month"], row["year"], row["price_per_night"]
        score = year * 100 + month  # e.g. 202507
        record = json.dumps({
            "hotel": hotel, "location": location,
            "month": month, "year": year, "price": price,
        })
        key = f"{_PRICING_PREFIX}:{_normalize(hotel)}"
        loc_key = f"{_LOCATION_PRICING}:{_normalize(location)}"
        member = f"{hotel}|{location}|{year}-{month:02d}"
        pipe.hset(f"market:pricing_record:{member}", mapping={"data": record})
        pipe.zadd(key, {member: score})
        pipe.zadd(loc_key, {member: score})
        synced += 1

    # Past negotiations
    rows = conn.execute(
        "SELECT hotel, location, month, year, starting_price, negotiation_price, proposed_price FROM past_negotiations"
    ).fetchall()
    for row in rows:
        hotel, location = row["hotel"], row["location"]
        month, year = row["month"], row["year"]
        score = year * 100 + month
        record = json.dumps({
            "hotel": hotel, "location": location,
            "month": month, "year": year,
            "starting_price": row["starting_price"],
            "negotiated_price": row["negotiation_price"],
            "proposed_price": row["proposed_price"],
            "discount_pct": round((1 - row["negotiation_price"] / row["starting_price"]) * 100, 1),
        })
        key = f"{_DEALS_PREFIX}:{_normalize(hotel)}"
        loc_key = f"{_LOCATION_DEALS}:{_normalize(location)}"
        member = f"{hotel}|{location}|{year}-{month:02d}"
        pipe.hset(f"market:deals_record:{member}", mapping={"data": record})
        pipe.zadd(key, {member: score})
        pipe.zadd(loc_key, {member: score})
        synced += 1

    pipe.set(_SYNC_SENTINEL, "1")
    pipe.execute()
    conn.close()
    if synced:
        logger.info("sync_market_data_to_redis: synced %d records", synced)
    return synced


def _normalize(s: str) -> str:
    return s.strip().lower().replace(" ", "_")


# ─── Retrieval for the negotiation brain ─────────────────────────────

def get_market_context(
    hotel_name: str,
    location: str,
    check_in_month: int | None = None,
) -> str:
    """Build a concise market brief from Redis for the negotiation brain.

    Returns a human-readable string suitable for injection into the LLM prompt.
    Covers:
      1. Historic pricing for this hotel (seasonal range)
      2. Past negotiation outcomes for this hotel (discount achieved)
      3. Competitor pricing in the same location
      4. Competitor negotiation outcomes in the same location
    """
    r = get_redis()
    lines: list[str] = []

    # 1. This hotel's historic pricing
    hotel_key = f"{_PRICING_PREFIX}:{_normalize(hotel_name)}"
    hotel_pricing = _fetch_records(r, hotel_key)
    if hotel_pricing:
        prices = [p["price"] for p in hotel_pricing]
        lines.append(
            f"Historic rates for {hotel_name}: "
            f"${min(prices):.0f}-${max(prices):.0f}/night "
            f"(avg ${sum(prices)/len(prices):.0f})"
        )
        if check_in_month:
            nearby = [p for p in hotel_pricing if _is_near_month(p["month"], check_in_month)]
            if nearby:
                nearby_prices = [p["price"] for p in nearby]
                avg_nearby = sum(nearby_prices) / len(nearby_prices)
                # Compute trend: avg of months before check_in vs at/after
                before = [p["price"] for p in nearby if p["month"] < check_in_month]
                at_or_after = [p["price"] for p in nearby if p["month"] >= check_in_month]
                if before and at_or_after:
                    avg_before = sum(before) / len(before)
                    avg_after = sum(at_or_after) / len(at_or_after)
                    diff_pct = (avg_after - avg_before) / avg_before * 100
                    if diff_pct > 5:
                        trend = "trending up"
                    elif diff_pct < -5:
                        trend = "trending down"
                    else:
                        trend = "stable"
                else:
                    trend = "stable"
                lines.append(
                    f"  Rates near month {check_in_month} (+/-2mo): "
                    f"${min(nearby_prices):.0f}-${max(nearby_prices):.0f}/night "
                    f"(avg ${avg_nearby:.0f}), {trend}"
                )

    # 2. This hotel's past negotiation outcomes
    deals_key = f"{_DEALS_PREFIX}:{_normalize(hotel_name)}"
    hotel_deals = _fetch_records(r, deals_key)
    if hotel_deals:
        discounts = [d["discount_pct"] for d in hotel_deals]
        avg_discount = sum(discounts) / len(discounts)
        lines.append(
            f"Past deals with {hotel_name}: "
            f"avg {avg_discount:.1f}% discount from initial rate "
            f"({len(hotel_deals)} negotiation(s))"
        )
        # Show the most recent deal
        latest = hotel_deals[-1]
        lines.append(
            f"  Latest deal: ${latest['starting_price']:.0f} → ${latest['negotiated_price']:.0f}/night "
            f"({latest['discount_pct']:.0f}% off)"
        )
        # Discount ceiling from nearby-month negotiations
        if check_in_month:
            nearby_deals = [d for d in hotel_deals if _is_near_month(d["month"], check_in_month)]
            if nearby_deals:
                best_discount = max(d["discount_pct"] for d in nearby_deals)
                lines.append(f"  Best seasonal discount: {best_discount:.0f}% — use as target ceiling")

    # 3. Competitor pricing in same location
    loc_key = f"{_LOCATION_PRICING}:{_normalize(location)}"
    loc_pricing = _fetch_records(r, loc_key)
    competitors = [p for p in loc_pricing if _normalize(p["hotel"]) != _normalize(hotel_name)]
    if competitors:
        lines.append(f"Competitor rates in {location}:")
        # Group by hotel
        by_hotel: dict[str, list[float]] = {}
        for p in competitors:
            by_hotel.setdefault(p["hotel"], []).append(p["price"])
        for h, prices in sorted(by_hotel.items()):
            lines.append(f"  {h}: ${min(prices):.0f}-${max(prices):.0f}/night")

    # 4. Competitor deals in same location
    loc_deals_key = f"{_LOCATION_DEALS}:{_normalize(location)}"
    loc_deals = _fetch_records(r, loc_deals_key)
    comp_deals = [d for d in loc_deals if _normalize(d["hotel"]) != _normalize(hotel_name)]
    if comp_deals:
        avg_comp_discount = sum(d["discount_pct"] for d in comp_deals) / len(comp_deals)
        lines.append(
            f"Competitor negotiation outcomes in {location}: "
            f"avg {avg_comp_discount:.1f}% discount across {len(comp_deals)} deal(s)"
        )

    if not lines:
        return ""

    return "Market intelligence:\n" + "\n".join(lines)


def _fetch_records(r, sorted_set_key: str, limit: int = 50) -> list[dict]:
    """Fetch all members from a sorted set and resolve their data hashes."""
    try:
        members = r.zrange(sorted_set_key, 0, limit - 1)
        if not members:
            return []
        # Determine hash prefix from the sorted set key
        if "pricing" in sorted_set_key:
            prefix = "market:pricing_record"
        else:
            prefix = "market:deals_record"
        pipe = r.pipeline()
        for m in members:
            pipe.hget(f"{prefix}:{m}", "data")
        results = pipe.execute()
        return [json.loads(r) for r in results if r]
    except Exception as exc:
        logger.warning("_fetch_records failed for %s: %s", sorted_set_key, exc)
        return []

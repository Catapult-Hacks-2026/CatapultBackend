from __future__ import annotations

from datetime import datetime, timezone

from app.hotel.schemas import HotelTarget
from app.memory.feature_schemas import HotelBehavioralProfile

_MAX_URGENCY_DAYS = 30   # beyond this, urgency score is 0
_MIN_URGENCY_DAYS = 2    # within this, urgency score is 1.0


def score_job_priority(
    target: HotelTarget,
    market_signals: dict,
    memory_profile: HotelBehavioralProfile,
) -> float:
    """Return a priority score 0-1. Higher = should run sooner."""

    # 1. Expected savings: gap between max_rate and target_rate relative to max_rate
    rate_gap = target.max_rate - target.target_rate
    savings_score = min(rate_gap / target.max_rate, 1.0) if target.max_rate > 0 else 0.0

    # 2. Urgency: days until check-in
    urgency_score = _urgency(target.check_in)

    # 3. Success probability from memory
    success_score = memory_profile.success_rate if memory_profile.total_calls > 0 else 0.5

    # 4. Market signal boost (e.g. low occupancy = hotel more likely to negotiate)
    occupancy = market_signals.get("occupancy_rate", 0.7)
    market_score = 1.0 - occupancy  # lower occupancy = better negotiation odds

    # 5. Quote staleness: if we have a recent quote, deprioritize (avoid redundancy)
    staleness_score = _staleness(market_signals.get("last_quoted_at"))

    # Weighted sum
    score = (
        savings_score   * 0.30
        + urgency_score * 0.25
        + success_score * 0.20
        + market_score  * 0.15
        + staleness_score * 0.10
    )

    # Apply the target's own priority_score as a multiplier hint (0.5–1.5 range)
    priority_multiplier = 0.5 + target.priority_score
    return min(score * priority_multiplier, 1.0)


def _urgency(check_in_str: str) -> float:
    try:
        check_in = datetime.fromisoformat(check_in_str).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0.5
    days_until = (check_in - datetime.now(timezone.utc)).days
    if days_until <= _MIN_URGENCY_DAYS:
        return 1.0
    if days_until >= _MAX_URGENCY_DAYS:
        return 0.0
    return 1.0 - (days_until - _MIN_URGENCY_DAYS) / (_MAX_URGENCY_DAYS - _MIN_URGENCY_DAYS)


def _staleness(last_quoted_at: str | None) -> float:
    if last_quoted_at is None:
        return 1.0  # never quoted: fully prioritize
    try:
        last = datetime.fromisoformat(last_quoted_at).replace(tzinfo=timezone.utc)
    except ValueError:
        return 1.0
    hours_since = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    # Fully stale after 48 hours; fully fresh within 4 hours
    return min(max((hours_since - 4) / 44, 0.0), 1.0)

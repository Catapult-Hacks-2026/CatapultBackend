from __future__ import annotations

from dataclasses import dataclass

from app.hotel.schemas import HotelQuote, HotelTarget


@dataclass
class HotelScoreBreakdown:
    rate_score: float        # 0-1: how well nightly_rate fits vs target/max
    inclusion_bonus: float   # 0-0.2: breakfast, wifi bonuses
    flexibility_score: float # 0-0.2: cancellation flexibility
    total: float             # weighted sum, 0-1


def score_hotel_quote(quote: HotelQuote, target: HotelTarget) -> HotelScoreBreakdown:
    # Rate score: 1.0 at or below target, 0.0 at or above max_rate
    rate_range = target.max_rate - target.target_rate
    if rate_range <= 0:
        rate_score = 1.0 if quote.nightly_rate <= target.target_rate else 0.0
    else:
        overage = quote.nightly_rate - target.target_rate
        rate_score = max(0.0, 1.0 - (overage / rate_range))

    # Inclusion bonuses
    inclusion_bonus = 0.0
    if quote.inclusions.get("breakfast"):
        inclusion_bonus += 0.1
    if quote.inclusions.get("wifi"):
        inclusion_bonus += 0.05
    if quote.inclusions.get("parking"):
        inclusion_bonus += 0.05
    inclusion_bonus = min(inclusion_bonus, 0.2)

    # Flexibility score based on cancellation policy keywords
    policy_lower = quote.cancellation_policy.lower()
    if "free cancellation" in policy_lower or "fully refundable" in policy_lower:
        flexibility_score = 0.2
    elif "24 hour" in policy_lower or "24-hour" in policy_lower:
        flexibility_score = 0.1
    elif "non-refundable" in policy_lower or "no refund" in policy_lower:
        flexibility_score = 0.0
    else:
        flexibility_score = 0.05

    total = (rate_score * 0.7) + (inclusion_bonus * 0.5) + (flexibility_score * 0.5)
    total = min(total, 1.0)

    return HotelScoreBreakdown(
        rate_score=rate_score,
        inclusion_bonus=inclusion_bonus,
        flexibility_score=flexibility_score,
        total=total,
    )


def is_acceptable(breakdown: HotelScoreBreakdown, threshold: float = 0.6) -> bool:
    return breakdown.total >= threshold

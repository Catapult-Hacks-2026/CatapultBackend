import numpy as np
from app.models.schemas import BuyerConfig, VendorOffer, ScoringBreakdown


def normalize(value: float, best: float, worst: float) -> float:
    """Normalize a value to [0, 1] where best=1.0, worst=0.0.
    
    Works for both "lower is better" (price) and "higher is better" (payment terms).
    """
    if best == worst:
        return 1.0
    return max(0.0, min(1.0, (worst - value) / (worst - best)))


def score_offer(offer: VendorOffer, config: BuyerConfig) -> ScoringBreakdown:
    """Score a vendor offer against buyer preferences using weighted dot product.
    
    Each variable is normalized to [0,1] based on the buyer's target (best)
    and hard ceiling (worst). The final utility is the dot product of
    normalized scores and buyer weights.
    """
    # Normalize each variable: 1.0 = target hit, 0.0 = at hard limit
    raw = {
        "unit_price": normalize(
            offer.unit_price,
            best=config.target_unit_price,
            worst=config.max_unit_price,
        ),
        "shipping_cost": normalize(
            offer.shipping_cost,
            best=config.target_shipping_cost,
            worst=config.max_shipping_cost,
        ),
        "payment_terms": normalize(
            offer.payment_terms_days,
            best=config.preferred_payment_terms,
            worst=config.min_payment_terms,
        ),
        "delivery": normalize(
            offer.delivery_days,
            best=config.preferred_delivery_days,
            worst=config.max_delivery_days,
        ),
    }

    score_vector = np.array(list(raw.values()))
    weight_vector = np.array([
        config.weight_price,
        config.weight_shipping,
        config.weight_payment_terms,
        config.weight_delivery,
    ])

    weighted = score_vector * weight_vector
    total = float(np.dot(score_vector, weight_vector))

    weighted_dict = dict(zip(raw.keys(), weighted.tolist()))

    return ScoringBreakdown(
        raw_scores=raw,
        weighted_scores=weighted_dict,
        total_utility=round(total, 4),
        meets_threshold=total >= config.min_acceptable_utility,
    )


def suggest_pivot(
    offer: VendorOffer,
    config: BuyerConfig,
    breakdown: ScoringBreakdown,
) -> dict:
    """If the overall utility is below threshold, identify which variable
    change would most efficiently bring it above threshold.
    
    Returns a dict of suggested counter-offer adjustments.
    """
    if breakdown.meets_threshold:
        return {}

    gap = config.min_acceptable_utility - breakdown.total_utility
    suggestions = {}

    # Rank variables by: (weight * room_to_improve) — i.e., which lever has the most upside
    variables = [
        ("unit_price", config.weight_price, breakdown.raw_scores["unit_price"]),
        ("shipping_cost", config.weight_shipping, breakdown.raw_scores["shipping_cost"]),
        ("payment_terms", config.weight_payment_terms, breakdown.raw_scores["payment_terms"]),
        ("delivery", config.weight_delivery, breakdown.raw_scores["delivery"]),
    ]

    ranked = sorted(variables, key=lambda x: x[1] * (1.0 - x[2]), reverse=True)

    remaining_gap = gap
    for var_name, weight, current_score in ranked:
        if remaining_gap <= 0:
            break
        max_gain = weight * (1.0 - current_score)
        if max_gain > 0:
            needed_improvement = min(remaining_gap / weight, 1.0 - current_score)
            suggestions[var_name] = {
                "current_normalized": round(current_score, 3),
                "needed_normalized": round(current_score + needed_improvement, 3),
                "potential_utility_gain": round(needed_improvement * weight, 4),
            }
            remaining_gap -= needed_improvement * weight

    return suggestions

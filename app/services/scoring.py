import numpy as np

from app.models.schemas import BuyerConfig, ScoringBreakdown, VendorOffer


def normalize(value: float, best: float, worst: float) -> float:
    if best == worst:
        return 1.0
    return max(0.0, min(1.0, (worst - value) / (worst - best)))


def score_offer(offer: VendorOffer, config: BuyerConfig) -> ScoringBreakdown:
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
    weight_vector = np.array(
        [
            config.weight_price,
            config.weight_shipping,
            config.weight_payment_terms,
            config.weight_delivery,
        ]
    )
    weighted = score_vector * weight_vector
    total = float(np.dot(score_vector, weight_vector))
    return ScoringBreakdown(
        raw_scores=raw,
        weighted_scores=dict(zip(raw.keys(), weighted.tolist())),
        total_utility=round(total, 4),
        meets_threshold=total >= config.min_acceptable_utility,
    )


def suggest_pivot(
    offer: VendorOffer,
    config: BuyerConfig,
    breakdown: ScoringBreakdown,
) -> dict:
    if breakdown.meets_threshold:
        return {}

    gap = config.min_acceptable_utility - breakdown.total_utility
    suggestions = {}
    variables = [
        ("unit_price", config.weight_price, breakdown.raw_scores["unit_price"]),
        ("shipping_cost", config.weight_shipping, breakdown.raw_scores["shipping_cost"]),
        ("payment_terms", config.weight_payment_terms, breakdown.raw_scores["payment_terms"]),
        ("delivery", config.weight_delivery, breakdown.raw_scores["delivery"]),
    ]
    ranked = sorted(variables, key=lambda item: item[1] * (1.0 - item[2]), reverse=True)

    remaining_gap = gap
    for var_name, weight, current_score in ranked:
        if remaining_gap <= 0 or weight <= 0:
            break
        max_gain = weight * (1.0 - current_score)
        if max_gain <= 0:
            continue
        needed_improvement = min(remaining_gap / weight, 1.0 - current_score)
        suggestions[var_name] = {
            "current_normalized": round(current_score, 3),
            "needed_normalized": round(current_score + needed_improvement, 3),
            "potential_utility_gain": round(needed_improvement * weight, 4),
        }
        remaining_gap -= needed_improvement * weight

    return suggestions

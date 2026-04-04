from typing import Optional

from app.models.enums import Strategy
from app.models.schemas import BuyerConfig, VendorOffer
from app.services.scoring import score_offer


def _seller_step(seller_config: dict, round_number: int, buyer_counter: Optional[VendorOffer]) -> VendorOffer:
    opening_price = float(seller_config.get("opening_price", seller_config.get("target_unit_price", 100.0)))
    min_unit_price = float(seller_config.get("min_unit_price", opening_price * 0.8))
    current_price = buyer_counter.unit_price if buyer_counter else opening_price
    concession = max((current_price - min_unit_price) * 0.35, 0.5)
    next_price = max(min_unit_price, round(current_price - concession, 2))
    return VendorOffer(
        unit_price=next_price,
        shipping_cost=0.0 if next_price <= seller_config.get("willing_to_offer_free_shipping_below", next_price) else 150.0,
        payment_terms_days=int(seller_config.get("payment_terms_days", 30)),
        delivery_days=int(seller_config.get("delivery_days", 14)),
        notes=f"seller_round_{round_number}",
    )


def _buyer_step(buyer_config: BuyerConfig, offer: VendorOffer) -> dict:
    score = score_offer(offer, buyer_config)
    if score.meets_threshold:
        return {
            "action": "accept",
            "offer": offer,
            "reasoning": "Offer meets buyer threshold.",
            "score": score.model_dump(),
        }

    counter_price = max(
        buyer_config.target_unit_price,
        round((offer.unit_price + buyer_config.target_unit_price) / 2, 2),
    )
    counter = VendorOffer(
        unit_price=min(counter_price, buyer_config.max_unit_price),
        shipping_cost=min(offer.shipping_cost, buyer_config.max_shipping_cost),
        payment_terms_days=max(offer.payment_terms_days, buyer_config.min_payment_terms),
        delivery_days=min(offer.delivery_days, buyer_config.max_delivery_days),
        notes="buyer_counter",
    )
    return {
        "action": "counter",
        "offer": counter,
        "reasoning": "Buyer countered based on current utility gap.",
        "score": score.model_dump(),
    }


def run_adversarial_negotiation(
    buyer_config: BuyerConfig,
    seller_config: dict,
    max_rounds: int = 10,
) -> list[dict]:
    log: list[dict] = []
    buyer_counter = None
    for round_number in range(1, max_rounds + 1):
        seller_offer = _seller_step(seller_config, round_number, buyer_counter)
        buyer_step = _buyer_step(buyer_config, seller_offer)
        log.append(
            {
                "round": round_number,
                "seller_offer": seller_offer.model_dump(),
                "buyer_action": buyer_step["action"],
                "buyer_offer": buyer_step["offer"].model_dump(),
                "buyer_reasoning": buyer_step["reasoning"],
                "score": buyer_step["score"],
                "strategy": Strategy.BALANCED.value,
            }
        )
        if buyer_step["action"] == "accept":
            break
        buyer_counter = buyer_step["offer"]
    return log

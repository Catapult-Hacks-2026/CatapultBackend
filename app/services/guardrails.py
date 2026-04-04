from typing import Optional

from app.models.schemas import AgentAction, BuyerConfig, GuardrailResult, VendorOffer
from app.services.scoring import score_offer


def _contains_acceptance_language(message: str) -> bool:
    lowered = message.lower()
    phrases = ("i accept", "deal", "agreed")
    return any(phrase in lowered for phrase in phrases)


def validate_agent_action(
    action: AgentAction,
    config: BuyerConfig,
    current_offer: Optional[VendorOffer] = None,
    round_number: int = 1,
) -> GuardrailResult:
    violations: list[str] = []
    counter = action.counter_offer

    if counter is not None:
        if counter.unit_price > config.max_unit_price:
            violations.append("Counter-offer unit price exceeds buyer max unit price.")
        if counter.shipping_cost > config.max_shipping_cost:
            violations.append("Counter-offer shipping cost exceeds buyer max shipping cost.")
        if counter.payment_terms_days < config.min_payment_terms:
            violations.append("Counter-offer payment terms are below buyer minimum.")
        if counter.delivery_days > config.max_delivery_days:
            violations.append("Counter-offer delivery timeline exceeds buyer maximum.")

    if action.should_accept:
        if round_number <= 1:
            violations.append("Agent must not accept on round 1.")
        offer_to_accept = current_offer or counter
        if offer_to_accept is None:
            violations.append("Agent cannot accept without an offer to accept.")
        else:
            score = score_offer(offer_to_accept, config)
            if score.total_utility < config.min_acceptable_utility:
                violations.append("Accepted offer does not meet minimum acceptable utility.")

    if _contains_acceptance_language(action.message) and not action.should_accept:
        violations.append("Message contains acceptance language while should_accept is false.")

    return GuardrailResult(
        passed=not violations,
        violations=violations,
        adjusted_action=action if not violations else None,
    )

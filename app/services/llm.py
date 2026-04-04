import json
import re
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - depends on optional install state
    Anthropic = None

from app.core.config import get_settings
from app.models.enums import Strategy
from app.models.schemas import AgentAction, VendorOffer

settings = get_settings()

_STRATEGY_INSTRUCTIONS = {
    Strategy.AGGRESSIVE.value: "Anchor low, push hard on concessions, and keep responses direct.",
    Strategy.BALANCED.value: "Negotiate firmly but collaboratively and trade concessions deliberately.",
    Strategy.VOLUME.value: "Emphasize order size and repeat business to press for better pricing.",
    Strategy.RELATIONSHIP.value: "Prioritize long-term partnership, reliability, and operational flexibility.",
}


def _client() -> Anthropic:
    if Anthropic is None:
        raise RuntimeError("anthropic package is not installed")
    return Anthropic(api_key=settings.anthropic_api_key)


def _extract_text(response: Any) -> str:
    parts = []
    for block in getattr(response, "content", []):
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def invoke_json(
    system_prompt: str,
    user_prompt: str,
    retry_prompt: bool = False,
    model: str | None = None,
    max_tokens: int = 1024,
) -> dict:
    if not settings.anthropic_api_key:
        raise RuntimeError("Anthropic API key is not configured")
    response = _client().messages.create(
        model=model or settings.negotiation_model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = _extract_text(response)
    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        if retry_prompt:
            raise
        stricter = (
            f"{user_prompt}\n\nReturn valid JSON only. Do not wrap the response in markdown."
        )
        return invoke_json(
            system_prompt,
            stricter,
            retry_prompt=True,
            model=model,
            max_tokens=max_tokens,
        )


def _fallback_agent_response(negotiation_context: dict) -> AgentAction:
    current_offer = VendorOffer.model_validate(negotiation_context["current_offer"])
    buyer_config = negotiation_context["buyer_config"]
    target_price = float(buyer_config["target_unit_price"])
    max_price = float(buyer_config["max_unit_price"])
    min_terms = int(buyer_config["min_payment_terms"])
    max_delivery = int(buyer_config["max_delivery_days"])
    utility = float(negotiation_context["scoring_breakdown"]["total_utility"])
    threshold = float(buyer_config["min_acceptable_utility"])

    if utility >= threshold and negotiation_context.get("round_number", 1) > 1:
        return AgentAction(
            message="Your latest offer is acceptable. We can move forward on these terms.",
            reasoning="Deterministic fallback accepted because the utility threshold was met.",
            should_accept=True,
        )

    counter = VendorOffer(
        unit_price=round(min(max(target_price, current_offer.unit_price * 0.94), max_price), 2),
        shipping_cost=min(current_offer.shipping_cost, float(buyer_config["max_shipping_cost"])),
        payment_terms_days=max(current_offer.payment_terms_days, min_terms),
        delivery_days=min(current_offer.delivery_days, max_delivery),
        notes="fallback_counter_offer",
    )
    return AgentAction(
        counter_offer=counter,
        message=(
            "We need a sharper commercial package. If you can align closer to our target price "
            "and preserve the operational terms in the counter, we can keep this moving."
        ),
        reasoning="Deterministic fallback countered because the utility threshold was not met yet.",
        should_accept=False,
        should_escalate=False,
    )


def _fallback_extract_offer(vendor_message: str) -> VendorOffer:
    numbers = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", vendor_message)]
    unit_price = numbers[0] if numbers else 100.0
    shipping_cost = numbers[1] if len(numbers) > 1 else 0.0
    payment_terms = int(numbers[2]) if len(numbers) > 2 else 30
    delivery_days = int(numbers[3]) if len(numbers) > 3 else 14
    return VendorOffer(
        unit_price=unit_price,
        shipping_cost=shipping_cost,
        payment_terms_days=payment_terms,
        delivery_days=delivery_days,
        notes="fallback_extracted_offer",
    )


def generate_agent_response(negotiation_context: dict) -> AgentAction:
    if not settings.anthropic_api_key or Anthropic is None:
        return _fallback_agent_response(negotiation_context)
    strategy = negotiation_context.get("strategy", Strategy.BALANCED.value)
    strategy_instruction = _STRATEGY_INSTRUCTIONS.get(
        str(strategy), _STRATEGY_INSTRUCTIONS[Strategy.BALANCED.value]
    )
    system_prompt = (
        "You are an AI procurement negotiator. Stay inside buyer constraints, respond as a buyer, "
        "and return JSON matching this schema: "
        '{"counter_offer": {"unit_price": number, "shipping_cost": number, "payment_terms_days": number, '
        '"delivery_days": number, "notes": string | null} | null, "message": string, "reasoning": string, '
        '"should_accept": boolean, "should_escalate": boolean}. '
        "Use any research_brief and competing_offers as negotiating leverage, but do not invent facts that are "
        "not present in the input context. "
        f"Strategy persona: {strategy_instruction}"
    )
    user_prompt = json.dumps(
        {
            "buyer_constraints": negotiation_context.get("buyer_config"),
            "current_scoring_breakdown": negotiation_context.get("scoring_breakdown"),
            "current_offer": negotiation_context.get("current_offer"),
            "round_number": negotiation_context.get("round_number"),
            "vendor_name": negotiation_context.get("vendor_name"),
            "conversation_history": negotiation_context.get("conversation_history"),
            "rag_memory": negotiation_context.get("rag_context"),
            "research_brief": negotiation_context.get("research_brief"),
            "competing_offers": negotiation_context.get("competing_offers"),
            "competitor_history": negotiation_context.get("competitor_history"),
            "pivot_suggestions": negotiation_context.get("pivot_suggestions"),
            "guardrail_feedback": negotiation_context.get("guardrail_feedback"),
        },
        indent=2,
        default=str,
    )
    payload = invoke_json(system_prompt, user_prompt)
    return AgentAction.model_validate(payload)


def extract_offer_from_message(vendor_message: str) -> VendorOffer:
    if not settings.anthropic_api_key or Anthropic is None:
        return _fallback_extract_offer(vendor_message)
    system_prompt = (
        "Extract a procurement offer from vendor text and return JSON only with keys "
        '{"unit_price": number, "shipping_cost": number, "payment_terms_days": integer, '
        '"delivery_days": integer, "notes": string | null}. Use best-effort inference and defaults when omitted.'
    )
    payload = invoke_json(system_prompt, vendor_message)
    return VendorOffer.model_validate(payload)

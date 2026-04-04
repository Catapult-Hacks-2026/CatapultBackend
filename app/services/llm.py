import json
import re
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - depends on optional install state
    Anthropic = None

from app.core.config import get_settings
from app.llm.client import invoke_json as anthropic_invoke_json
from app.llm.negotiation_brain import decide_move
from app.llm.prompts import FACT_EXTRACTION_SYSTEM, STRATEGY_INSTRUCTIONS
from app.models.enums import Strategy
from app.models.schemas import AgentAction, VendorOffer

settings = get_settings()

_STRATEGY_INSTRUCTIONS = STRATEGY_INSTRUCTIONS


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
    if not settings.anthropic_api_key or Anthropic is None:
        raise RuntimeError("Anthropic API key is not configured")
    try:
        return anthropic_invoke_json(
            system_prompt,
            user_prompt,
            model=model,
            max_tokens=max_tokens,
            temperature=0.2,
        )
    except Exception:
        if retry_prompt:
            raise
        stricter = f"{user_prompt}\n\nReturn valid JSON only."
        return invoke_json(system_prompt, stricter, True, model, max_tokens)


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
    return decide_move(negotiation_context)


def extract_offer_from_message(vendor_message: str) -> VendorOffer:
    if not settings.anthropic_api_key or Anthropic is None:
        return _fallback_extract_offer(vendor_message)
    payload = invoke_json(FACT_EXTRACTION_SYSTEM, vendor_message)
    return VendorOffer.model_validate(payload)


def extract_facts_fast(vendor_message: str) -> VendorOffer:
    return _fallback_extract_offer(vendor_message)


def decide_move_fast(negotiation_context: dict) -> AgentAction:
    return _fallback_agent_response(negotiation_context)


def generate_reply_fast(negotiation_context: dict) -> str:
    return decide_move_fast(negotiation_context).message

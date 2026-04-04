from __future__ import annotations

import re

from app.email.schemas import EmailExtractionResult, EmailSessionState
from app.hotel.schemas import HotelQuote
from app.llm.openai_client import invoke_json
from app.llm.prompts import EMAIL_FACT_EXTRACTION_SYSTEM

_PRICE_RE = re.compile(r"\$?\s*(\d{2,4}(?:\.\d{2})?)")


async def extract_facts_from_email(
    message_text: str,
    session_state: EmailSessionState,
) -> EmailExtractionResult:
    transcript_lines = session_state.transcript[-6:] if len(session_state.transcript) >= 6 else session_state.transcript
    conversation = "\n".join(f"{item['role']}: {item['content']}" for item in transcript_lines)
    user_prompt = f"Recent thread:\n{conversation}\n\nLatest email:\n{message_text}"

    try:
        data = await invoke_json(EMAIL_FACT_EXTRACTION_SYSTEM, user_prompt, temperature=0.0)
    except Exception:
        data = _heuristic_extract(message_text)

    quote = None
    if data.get("nightly_rate") is not None:
        quote = HotelQuote(
            nightly_rate=float(data["nightly_rate"]),
            total_rate=float(data.get("total_rate") or data["nightly_rate"]),
            inclusions=data.get("inclusions") or {},
            cancellation_policy=data.get("cancellation_policy") or "",
            rate_type=data.get("rate_type") or "",
            fees=float(data.get("fees") or 0),
            raw_text=data.get("raw_text") or message_text,
        )

    return EmailExtractionResult(
        quote=quote,
        raw_text=data.get("raw_text") or message_text,
        confidence=float(data.get("confidence", 0.35)),
        booking_ready=bool(data.get("booking_ready", False)),
        no_availability=bool(data.get("no_availability", False)),
        requests_human_action=bool(data.get("requests_human_action", False)),
    )


def _heuristic_extract(message_text: str) -> dict:
    matches = _PRICE_RE.findall(message_text)
    nightly_rate = float(matches[0]) if matches else None
    lowered = message_text.lower()
    return {
        "nightly_rate": nightly_rate,
        "total_rate": nightly_rate,
        "inclusions": {
            "breakfast": "breakfast" in lowered,
            "wifi": "wifi" in lowered,
            "parking": "parking" in lowered,
        },
        "cancellation_policy": "",
        "rate_type": "email_quote" if nightly_rate is not None else "",
        "fees": 0,
        "raw_text": message_text,
        "booking_ready": "confirm" in lowered and "reservation" in lowered,
        "no_availability": "no availability" in lowered or "sold out" in lowered,
        "requests_human_action": "contract" in lowered or "credit card" in lowered,
        "confidence": 0.55 if nightly_rate is not None else 0.2,
    }

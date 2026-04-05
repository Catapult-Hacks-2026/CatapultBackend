from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.artifacts.schemas import NegotiatedRateAgreement, ReceiptArtifacts
from app.email.pdf_utils import render_text_pdf
from app.llm.openai_client import invoke_json
from app.llm.prompts import EMAIL_CONTRACT_GENERATION_SYSTEM

logger = logging.getLogger(__name__)

ARTIFACT_ROOT = Path("data/email_artifacts")
DETAILS_URL_PLACEHOLDER = "https://example.com/galileo/reports"
DEMO_REPORT_RECIPIENT = "jeffreytseng07@gmail.com"
RECEIPT_RECIPIENT_KEYS = (
    "receipt_email",
    "traveler_email",
    "travel_agent_email",
    "agent_email",
    "user_email",
    "recipient_email",
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged[key] = _deep_merge(base[key], value)
        else:
            merged[key] = value
    return merged


def _session_target(session_state: Any) -> Any:
    return getattr(session_state, "email_target", None) or getattr(session_state, "hotel_target", None)


def _report_summary(session_state: Any) -> str:
    value = getattr(session_state, "report_summary", "")
    return value.strip() if isinstance(value, str) else ""


def _market_context(session_state: Any) -> dict[str, Any]:
    target = _session_target(session_state)
    return getattr(target, "market_context", {}) or {}


def _metadata(session_state: Any) -> dict[str, Any]:
    target = _session_target(session_state)
    return getattr(target, "campaign_metadata", {}) or {}


def _format_rate(rate: float | str) -> str:
    if isinstance(rate, (int, float)):
        return f"${rate:,.0f} USD/night"
    return str(rate)


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() and value.strip().lower() not in {"n/a", "none", "null", "unknown"}


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return _has_text(value)
    return value is not None


def _summary_text(session_state: Any) -> str:
    summary = _report_summary(session_state)
    if summary:
        return summary
    transcript = getattr(session_state, "transcript", None) or []
    if transcript:
        latest = transcript[-1].get("content", "")
        if isinstance(latest, str):
            return latest.strip()
    return ""


def _extract_first_rate(text: str) -> float | None:
    match = re.search(r"\$?\s*(\d{2,4}(?:\.\d{1,2})?)\s*(?:usd|dollars)?\s*(?:/ ?night|per night|nightly)?", text, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_percentage(text: str) -> str:
    match = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", text)
    return f"{match.group(1)}%" if match else "N/A"


def _extract_cancellation_policy(text: str) -> str:
    patterns = [
        r"(\d+\s*(?:hour|hours|day|days)\s+prior)",
        r"(same day\s+\d{1,2}:\d{2}\s*(?:am|pm)?)",
        r"(non-refundable)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return "N/A"


def _extract_concessions(text: str) -> list[str]:
    lowered = text.lower()
    concessions: list[str] = []
    if "breakfast" in lowered:
        concessions.append("Breakfast included")
    if "wifi" in lowered or "wi-fi" in lowered:
        concessions.append("Complimentary Wi-Fi")
    if "parking" in lowered:
        concessions.append("Parking included")
    if "late checkout" in lowered:
        concessions.append("Late checkout")
    return concessions


def _extract_inventory_guarantee(text: str) -> str:
    lowered = text.lower()
    if "last room availability" in lowered or re.search(r"\blra\b", lowered):
        return "LRA (Last Room Availability)"
    return "NLRA (Non-Last Room Availability)"


def _summary_hints(session_state: Any) -> dict[str, Any]:
    summary = _summary_text(session_state)
    if not summary:
        return {}

    hints: dict[str, Any] = {}
    rate = _extract_first_rate(summary)
    if rate is not None:
        hints["rate"] = rate
    discount = _extract_percentage(summary)
    if discount != "N/A":
        hints["discount"] = discount
    cancellation_policy = _extract_cancellation_policy(summary)
    if cancellation_policy != "N/A":
        hints["cancellation_policy"] = cancellation_policy
    concessions = _extract_concessions(summary)
    if concessions:
        hints["concessions"] = concessions
    hints["inventory_guarantee"] = _extract_inventory_guarantee(summary)
    return hints


def _infer_location(contract: NegotiatedRateAgreement, session_state: Any = None) -> str:
    if session_state is not None:
        metadata = _metadata(session_state)
        market_context = _market_context(session_state)
        for key in ("location", "city", "market", "destination"):
            value = metadata.get(key) or market_context.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return contract.parties.vendorName


def _conversation_summary(contract: NegotiatedRateAgreement, session_state: Any = None, outcome: str | None = None) -> str:
    if session_state is not None:
        summary = _report_summary(session_state)
        if summary:
            return summary
    rate_item = contract.rateMatrix[0] if contract.rateMatrix else None
    rate_text = _format_rate(rate_item.negotiatedRateUSD) if rate_item else "N/A"
    concessions = ", ".join(contract.concessions or ["None"])
    cancellation = contract.criticalClauses.cancellationPolicy or "N/A"
    outcome_text = (outcome or "").replace("_", " ").strip() or "negotiation completed"
    if "failed" in outcome_text or "closed" in outcome_text:
        return (
            f"Galileo completed the conversation with {contract.parties.vendorName}, but the final commercial position "
            f"remained at {rate_text}. Concessions discussed were {concessions}, with a cancellation policy of "
            f"{cancellation}. Outcome: {outcome_text}."
        )
    return (
        f"Galileo negotiated with {contract.parties.vendorName} and the strongest position captured was {rate_text}. "
        f"Concessions include {concessions}, and the hotel confirmed a cancellation policy of {cancellation}. "
        f"Outcome: {outcome_text}."
    )


def _pricing_rationale_lines(contract: NegotiatedRateAgreement, session_state: Any = None) -> list[str]:
    lines: list[str] = []
    market_context = _market_context(session_state) if session_state is not None else {}
    summary_hints = _summary_hints(session_state) if session_state is not None else {}

    try:
        start = datetime.strptime(contract.term.startDate, "%Y-%m-%d")
        month = start.month
        if month in {5, 6, 7, 8, 9, 10}:
            season_note = "shoulder-to-high season demand is likely supporting firmer pricing."
        elif month in {11, 12}:
            season_note = "holiday travel patterns may be putting upward pressure on pricing."
        else:
            season_note = "off-peak seasonality appears to be moderating the rate posture."
        lines.append(f"Seasonality: {season_note}")
        if start.weekday() in {4, 5}:
            lines.append("Day pattern: the requested stay starts near a weekend, which can compress transient inventory.")
    except Exception:
        lines.append("Seasonality: no date-derived seasonality signal was available.")

    lines.append(
        f"Inventory posture: the agreement was captured under {contract.criticalClauses.inventoryGuarantee}, "
        "which materially affects pricing flexibility."
    )

    concessions = [item for item in contract.concessions if _has_text(item)]
    if concessions:
        lines.append(
            f"Concessions offset: {', '.join(concessions)} helped improve total trip value even if the base rate did not move further."
        )

    target = _session_target(session_state) if session_state is not None else None
    target_rate = getattr(target, "target_rate", None)
    if target_rate is not None and contract.rateMatrix:
        negotiated_rate = contract.rateMatrix[0].negotiatedRateUSD
        if isinstance(negotiated_rate, (int, float)):
            delta = negotiated_rate - target_rate
            if delta <= 0:
                lines.append("Rate posture: the negotiated rate met or beat the working target rate.")
            else:
                lines.append(
                    f"Rate posture: the hotel held ${delta:,.0f} above the working target, suggesting limited incremental flexibility."
                )

    market_note = market_context.get("pricing_context") or market_context.get("market_note") or market_context.get("demand_signal")
    if isinstance(market_note, str) and market_note.strip():
        lines.append(f"Market context: {market_note.strip()}")
    else:
        lines.append("Market context: no major citywide event or extraordinary demand signal was captured in the workflow.")

    weather_note = market_context.get("weather") or market_context.get("temperature") or market_context.get("climate_note")
    if isinstance(weather_note, str) and weather_note.strip():
        lines.append(f"Weather or temperature signal: {weather_note.strip()}")
    else:
        lines.append("Weather or temperature signal: no weather-driven pricing adjustment was explicitly identified.")

    geopolitical_note = market_context.get("geopolitical_event") or market_context.get("travel_advisory")
    if isinstance(geopolitical_note, str) and geopolitical_note.strip():
        lines.append(f"External risk factor: {geopolitical_note.strip()}")
    else:
        lines.append("External risk factor: no geopolitical or extraordinary external disruption was explicitly identified.")

    summary = _report_summary(session_state) if session_state is not None else ""
    if summary:
        lines.append(f"Call summary signal: {summary}")
    if summary_hints.get("rate") is not None:
        lines.append(f"Structured summary hint: the analyzer surfaced a working rate of ${summary_hints['rate']:,.0f}.")

    return lines


def resolve_receipt_recipient(session_state: Any) -> str:
    target = _session_target(session_state)
    metadata = getattr(target, "campaign_metadata", {}) or {}
    for key in RECEIPT_RECIPIENT_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            logger.info(
                "resolve_receipt_recipient: session_id=%s key=%s recipient=%s",
                getattr(session_state, "session_id", ""),
                key,
                value.strip(),
            )
            return value.strip()
    logger.info(
        "resolve_receipt_recipient: session_id=%s using_demo_recipient=%s",
        getattr(session_state, "session_id", ""),
        DEMO_REPORT_RECIPIENT,
    )
    return DEMO_REPORT_RECIPIENT


def build_receipt_email_subject(contract: NegotiatedRateAgreement, outcome: str | None = None) -> str:
    outcome_label = outcome or "completed"
    return f"Galileo negotiation report | {outcome_label} | {contract.galileoReferenceId}"


def build_receipt_email_body(
    contract: NegotiatedRateAgreement,
    session_state: Any = None,
    outcome: str | None = None,
    detail_url: str = DETAILS_URL_PLACEHOLDER,
) -> str:
    rate_item = contract.rateMatrix[0] if contract.rateMatrix else None
    prepared_at = _utc_timestamp()
    location = _infer_location(contract, session_state)
    details: list[str] = []
    if rate_item and _has_value(rate_item.negotiatedRateUSD):
        details.append(f"Rate: {_format_rate(rate_item.negotiatedRateUSD)}")
    if rate_item and _has_text(rate_item.roomOrFareType):
        details.append(f"Room Type: {rate_item.roomOrFareType}")
    if _has_text(contract.term.startDate) and _has_text(contract.term.endDate):
        details.append(f"Dates: {contract.term.startDate} to {contract.term.endDate}")
    if _has_text(location):
        details.append(f"Location: {location}")
    if _has_text(contract.criticalClauses.inventoryGuarantee):
        details.append(f"Inventory Guarantee: {contract.criticalClauses.inventoryGuarantee}")
    if _has_text(contract.criticalClauses.cancellationPolicy):
        details.append(f"Cancellation Policy: {contract.criticalClauses.cancellationPolicy}")
    concessions = [item for item in contract.concessions if _has_text(item)]
    if concessions:
        details.append(f"Concessions: {', '.join(concessions)}")
    if _has_text(contract.billingAndSettlement.method):
        details.append(f"Billing Method: {contract.billingAndSettlement.method}")
    details.append(f"Report Time: {prepared_at}")

    lines = [
        "Hello,",
        "",
        "After negotiating with the hotel, this is the best deal we have gotten:",
        "",
    ]
    lines.extend(details)
    summary = _conversation_summary(contract, session_state, outcome)
    if _has_text(summary):
        lines.extend([
            "",
            f"Conversation Summary: {summary}",
        ])
    lines.extend([
        "",
        f"If you wish to know more, view details at {detail_url}.",
        "",
        "Best,",
        "Galileo",
    ])
    return "\n".join(lines)


def render_contract_pdf_lines(contract: NegotiatedRateAgreement, session_state: Any = None, outcome: str | None = None) -> list[str]:
    location = _infer_location(contract, session_state)
    outcome_text = (outcome or "").replace("_", " ").strip() or "completed"
    lines = [
        f"Reference ID: {contract.galileoReferenceId}",
        f"Prepared: {_utc_timestamp()}",
        f"Negotiation Outcome: {outcome_text}",
        "",
        f"Client: {contract.parties.clientName}",
        f"Vendor: {contract.parties.vendorName}",
        f"Location: {location}",
        f"Term: {contract.term.startDate} to {contract.term.endDate}",
        "",
        "Executive Commercial Summary",
        _conversation_summary(contract, session_state, outcome),
        "",
        "Rate Matrix",
    ]
    for item in contract.rateMatrix:
        lines.append(
            f"{item.roomOrFareType} | Negotiated Rate: ${item.negotiatedRateUSD} USD | Discount from BAR: {item.discountFromBAR}"
        )
    lines.extend([
        "",
        "Pricing Rationale",
    ])
    lines.extend(_pricing_rationale_lines(contract, session_state))
    lines.extend([
        "",
        "Critical Clauses",
        f"Inventory Guarantee: {contract.criticalClauses.inventoryGuarantee}",
        f"Blackout Dates: {', '.join(contract.criticalClauses.blackoutDates or ['None'])}",
        f"Cancellation Policy: {contract.criticalClauses.cancellationPolicy}",
        "",
        "Concessions",
    ])
    if contract.concessions:
        for concession in contract.concessions:
            lines.append(f"- {concession}")
    else:
        lines.append("- None")
    transcript = getattr(session_state, "transcript", None)
    if transcript:
        lines.extend([
            "",
            "Negotiation Notes",
        ])
        for item in transcript[-4:]:
            role = str(item.get("role", "unknown")).title()
            content = str(item.get("content", "")).strip()
            if content:
                lines.append(f"{role}: {content}")
    lines.extend([
        "",
        "Billing and Settlement",
        f"Method: {contract.billingAndSettlement.method}",
    ])
    return lines


async def generate_contract_details(session_state: Any) -> NegotiatedRateAgreement:
    target = _session_target(session_state)
    metadata = getattr(target, "campaign_metadata", {}) or {}
    hotel_id = getattr(target, "hotel_id", "UNK")
    room_type = getattr(target, "room_type", "N/A")
    check_in = getattr(target, "check_in", "N/A")
    check_out = getattr(target, "check_out", "N/A")
    transcript_text = "\n".join(f"{item['role']}: {item['content']}" for item in session_state.transcript)
    high_level_summary = _report_summary(session_state)
    summary_hints = _summary_hints(session_state)
    quotes = [
        {
            "nightly_rate": quote.nightly_rate,
            "total_rate": quote.total_rate,
            "rate_type": quote.rate_type,
            "cancellation_policy": quote.cancellation_policy,
            "inclusions": quote.inclusions,
            "fees": quote.fees,
        }
        for quote in session_state.quotes_received
    ]
    hotel_name = session_state.behavioral_priors.get("hotel_name") or hotel_id
    prompt = "\n".join([
        f"Client name hint: {metadata.get('client_name', metadata.get('buyer_name', 'N/A'))}",
        f"Vendor name hint: {metadata.get('vendor_name', hotel_name)}",
        f"Requested stay: {check_in} to {check_out}",
        f"Requested room type: {room_type}",
        f"Negotiation outcome: {session_state.outcome.value if getattr(session_state, 'outcome', None) else 'N/A'}",
        f"High-level negotiation summary: {high_level_summary or 'N/A'}",
        f"Structured hints from summary: {json.dumps(summary_hints)}",
        f"Known quotes JSON: {json.dumps(quotes)}",
        "Negotiation transcript:",
        transcript_text,
    ])

    fallback_rate = session_state.quotes_received[-1].nightly_rate if session_state.quotes_received else summary_hints.get("rate", "N/A")
    fallback_cancellation = (
        session_state.quotes_received[-1].cancellation_policy
        if session_state.quotes_received and session_state.quotes_received[-1].cancellation_policy
        else summary_hints.get("cancellation_policy", "N/A")
    )
    fallback_concessions = summary_hints.get("concessions") or ["None"]
    fallback = {
        "documentTitle": "Corporate Negotiated Rate Agreement - 2026",
        "galileoReferenceId": f"GAL-{uuid4().hex[:4].upper()}-{hotel_id[:3].upper()}",
        "parties": {
            "clientName": str(metadata.get("client_name", metadata.get("buyer_name", "N/A"))),
            "vendorName": str(metadata.get("vendor_name", hotel_name)),
        },
        "term": {
            "startDate": check_in,
            "endDate": check_out,
        },
        "rateMatrix": [
            {
                "roomOrFareType": room_type,
                "negotiatedRateUSD": fallback_rate,
                "discountFromBAR": summary_hints.get("discount", "N/A"),
            }
        ],
        "criticalClauses": {
            "inventoryGuarantee": summary_hints.get("inventory_guarantee", "NLRA (Non-Last Room Availability)"),
            "blackoutDates": ["None"],
            "cancellationPolicy": fallback_cancellation,
        },
        "concessions": fallback_concessions,
        "billingAndSettlement": {"method": "N/A"},
    }

    try:
        data = await invoke_json(EMAIL_CONTRACT_GENERATION_SYSTEM, prompt, temperature=0.0, max_tokens=1400)
    except Exception as exc:
        logger.warning("contract generation failed for %s: %s", session_state.session_id, exc)
        data = fallback

    merged = _deep_merge(fallback, data)
    return NegotiatedRateAgreement.model_validate(merged)


async def build_contract_artifacts(session_state: Any) -> ReceiptArtifacts:
    contract = await generate_contract_details(session_state)
    session_state.contract_details = contract
    outcome = getattr(getattr(session_state, "outcome", None), "value", None)
    artifacts = persist_contract_artifacts(session_state.session_id, contract, session_state=session_state, outcome=outcome)
    session_state.receipt_artifacts = artifacts
    return artifacts


def persist_contract_artifacts(
    session_id: str,
    contract: NegotiatedRateAgreement,
    session_state: Any = None,
    outcome: str | None = None,
) -> ReceiptArtifacts:
    artifact_dir = ARTIFACT_ROOT / session_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    json_path = artifact_dir / "negotiated_rate_agreement.json"
    pdf_path = artifact_dir / "negotiated_rate_agreement.pdf"

    contract_json = contract.model_dump()
    json_path.write_text(json.dumps(contract_json, indent=2), encoding="utf-8")
    pdf_bytes = render_text_pdf(contract.documentTitle, render_contract_pdf_lines(contract, session_state, outcome))
    pdf_path.write_bytes(pdf_bytes)
    logger.info(
        "persist_contract_artifacts: session_id=%s json_path=%s pdf_path=%s outcome=%s",
        session_id,
        json_path,
        pdf_path,
        outcome,
    )

    return ReceiptArtifacts(
        contract_json=contract_json,
        json_path=str(json_path.resolve()),
        pdf_path=str(pdf_path.resolve()),
        email_sent_to="",
    )

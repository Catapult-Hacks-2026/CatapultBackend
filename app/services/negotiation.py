import json
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from app.core.database import get_db
from app.models.enums import MessageRole, NegotiationStatus
from app.models.schemas import AgentAction, BuyerConfig, VendorOffer
from app.services.guardrails import validate_agent_action
from app.services.llm import extract_offer_from_message, generate_agent_response
from app.services.rag import (
    add_negotiation_to_history,
    retrieve_competitor_context,
    retrieve_vendor_context,
)
from app.services.research import generate_negotiation_brief
from app.services.scoring import score_offer, suggest_pivot


def _parse_json_blob(payload: str | None) -> dict | list | None:
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _load_negotiation(negotiation_id: str):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM negotiations WHERE id = ?",
        (negotiation_id,),
    ).fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Negotiation not found")
    messages = conn.execute(
        "SELECT * FROM messages WHERE negotiation_id = ? ORDER BY created_at, id",
        (negotiation_id,),
    ).fetchall()
    conn.close()
    return row, messages


def create_negotiation_record(
    vendor_name: str,
    strategy: str,
    config: BuyerConfig,
    product_category: str = "general",
    max_rounds: int = 10,
):
    negotiation_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        """
        INSERT INTO negotiations (
            id,
            vendor_name,
            product_category,
            status,
            strategy,
            config,
            round_number,
            max_rounds,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            negotiation_id,
            vendor_name,
            product_category,
            NegotiationStatus.PENDING.value,
            strategy,
            config.model_dump_json(),
            max_rounds,
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM negotiations WHERE id = ?", (negotiation_id,)).fetchone()
    conn.close()
    return row


def _serialize_message(row) -> dict:
    structured = _parse_json_blob(row["structured_data"])
    rag_context = _parse_json_blob(row["rag_context"])
    guardrail_log = _parse_json_blob(row["guardrail_log"])
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "structured_data": structured,
        "utility_score": row["utility_score"],
        "rag_context": rag_context,
        "guardrail_log": guardrail_log,
        "created_at": row["created_at"],
    }


def _default_escalation_action() -> AgentAction:
    return AgentAction(
        message="This negotiation needs human review before we proceed further.",
        reasoning="Maximum round count reached or automatic negotiation failed validation.",
        should_escalate=True,
    )


def _persist_turn(
    negotiation_id: str,
    vendor_message: str,
    vendor_offer: VendorOffer,
    action: AgentAction,
    breakdown,
    rag_context: list[dict],
    guardrail_result,
    status: str,
) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO messages (negotiation_id, role, content, structured_data, utility_score, rag_context, guardrail_log)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            negotiation_id,
            MessageRole.VENDOR.value,
            vendor_message,
            json.dumps(vendor_offer.model_dump()),
            breakdown.total_utility,
            json.dumps(rag_context),
            None,
        ),
    )
    conn.execute(
        """
        INSERT INTO messages (negotiation_id, role, content, structured_data, utility_score, rag_context, guardrail_log)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            negotiation_id,
            MessageRole.AGENT.value,
            action.message,
            json.dumps(action.counter_offer.model_dump()) if action.counter_offer else None,
            breakdown.total_utility,
            json.dumps(rag_context),
            json.dumps(guardrail_result.model_dump()),
        ),
    )
    current_offer = action.counter_offer or vendor_offer
    conn.execute(
        """
        UPDATE negotiations
        SET round_number = round_number + 1,
            current_offer = ?,
            utility_score = ?,
            status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            json.dumps(current_offer.model_dump()),
            breakdown.total_utility,
            status,
            datetime.now(UTC).isoformat(),
            negotiation_id,
        ),
    )
    conn.commit()

    if status in {
        NegotiationStatus.ACCEPTED.value,
        NegotiationStatus.REJECTED.value,
        NegotiationStatus.ESCALATED.value,
    }:
        rows = conn.execute(
            "SELECT * FROM messages WHERE negotiation_id = ? ORDER BY created_at, id",
            (negotiation_id,),
        ).fetchall()
        negotiation = conn.execute(
            "SELECT * FROM negotiations WHERE id = ?",
            (negotiation_id,),
        ).fetchone()
        add_negotiation_to_history(
            negotiation_id=negotiation_id,
            vendor_name=negotiation["vendor_name"],
            product_category=negotiation["product_category"],
            messages=[_serialize_message(row) for row in rows],
            final_offer=current_offer,
            outcome=status,
        )
    conn.close()


def _load_competing_offers(
    negotiation_id: str,
    product_category: str,
    limit: int = 3,
) -> list[dict]:
    if not product_category:
        return []
    conn = get_db()
    rows = conn.execute(
        """
        SELECT vendor_name, current_offer, utility_score, round_number, updated_at
        FROM negotiations
        WHERE id != ?
          AND product_category = ?
          AND current_offer IS NOT NULL
          AND status NOT IN (?, ?, ?)
        ORDER BY utility_score DESC, updated_at DESC
        LIMIT ?
        """,
        (
            negotiation_id,
            product_category,
            NegotiationStatus.ACCEPTED.value,
            NegotiationStatus.REJECTED.value,
            NegotiationStatus.ESCALATED.value,
            limit,
        ),
    ).fetchall()
    conn.close()

    offers = []
    for row in rows:
        offer = _parse_json_blob(row["current_offer"])
        if not isinstance(offer, dict):
            continue
        offers.append(
            {
                "vendor_name": row["vendor_name"],
                "offer": offer,
                "utility_score": row["utility_score"],
                "round_number": row["round_number"],
                "updated_at": row["updated_at"],
            }
        )
    return offers


def process_vendor_input(
    negotiation_id: str,
    vendor_message: str,
    vendor_offer: VendorOffer | None,
) -> dict:
    negotiation, message_rows = _load_negotiation(negotiation_id)
    config = BuyerConfig.model_validate_json(negotiation["config"])
    if vendor_offer is None:
        vendor_offer = extract_offer_from_message(vendor_message)

    breakdown = score_offer(vendor_offer, config)
    if negotiation["round_number"] >= negotiation["max_rounds"]:
        action = _default_escalation_action()
        guardrail_result = validate_agent_action(
            action,
            config,
            current_offer=vendor_offer,
            round_number=negotiation["round_number"] + 1,
        )
        _persist_turn(
            negotiation_id,
            vendor_message,
            vendor_offer,
            action,
            breakdown,
            [],
            guardrail_result,
            NegotiationStatus.ESCALATED.value,
        )
        return {
            "agent_message": action.message,
            "scoring_breakdown": breakdown.model_dump(),
            "rag_context_used": [],
            "guardrail_log": guardrail_result.model_dump(),
            "status": NegotiationStatus.ESCALATED.value,
        }

    pivots = suggest_pivot(vendor_offer, config, breakdown)
    current_offer_text = json.dumps(vendor_offer.model_dump())
    rag_context = retrieve_vendor_context(
        negotiation["vendor_name"], current_offer_text, top_k=5
    )
    research_brief = _parse_json_blob(negotiation["research_brief"])
    if research_brief is None:
        try:
            research_brief = generate_negotiation_brief(negotiation_id)
        except Exception:
            research_brief = None
    competing_offers = _load_competing_offers(
        negotiation_id,
        negotiation["product_category"],
    )
    competitor_history = retrieve_competitor_context(negotiation["product_category"], top_k=3)
    conversation_history = [_serialize_message(row) for row in message_rows]

    context = {
        "vendor_name": negotiation["vendor_name"],
        "product_category": negotiation["product_category"],
        "strategy": negotiation["strategy"],
        "round_number": negotiation["round_number"] + 1,
        "buyer_config": config.model_dump(),
        "conversation_history": conversation_history,
        "current_offer": vendor_offer.model_dump(),
        "scoring_breakdown": breakdown.model_dump(),
        "pivot_suggestions": pivots,
        "rag_context": rag_context,
        "research_brief": research_brief,
        "competing_offers": competing_offers,
        "competitor_history": competitor_history,
    }

    last_result = None
    action = None
    for _ in range(3):
        action = generate_agent_response(context)
        last_result = validate_agent_action(
            action,
            config,
            current_offer=vendor_offer,
            round_number=negotiation["round_number"] + 1,
        )
        if last_result.passed:
            break
        context["guardrail_feedback"] = last_result.model_dump()
    if action is None or last_result is None:
        raise RuntimeError("Negotiation pipeline failed to produce an action.")

    if not last_result.passed:
        action = _default_escalation_action()
        last_result = validate_agent_action(
            action,
            config,
            current_offer=vendor_offer,
            round_number=negotiation["round_number"] + 1,
        )

    status = NegotiationStatus.AWAITING_VENDOR.value
    if action.should_accept:
        status = NegotiationStatus.ACCEPTED.value
    elif action.should_escalate:
        status = NegotiationStatus.ESCALATED.value

    _persist_turn(
        negotiation_id,
        vendor_message,
        vendor_offer,
        action,
        breakdown,
        rag_context,
        last_result,
        status,
    )

    return {
        "agent_message": action.message,
        "agent_action": action.model_dump(),
        "scoring_breakdown": breakdown.model_dump(),
        "rag_context_used": rag_context,
        "guardrail_log": last_result.model_dump(),
        "status": status,
    }

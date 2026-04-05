from __future__ import annotations

import hashlib
import re
import sqlite3


NEGOTIATION_ENTERPRISE_ID = "__negotiation_enterprise__"
NEGOTIATION_EVENT_ID = "__negotiation_event__"


def negotiation_select(agent_alias: str = "ga") -> str:
    return f"""
        SELECT
            {agent_alias}.id,
            COALESCE({agent_alias}.company_name, '') AS vendor_name,
            'general' AS product_category,
            {agent_alias}.status,
            'balanced' AS strategy,
            NULL AS config,
            NULL AS current_offer,
            NULL AS research_brief,
            NULL AS utility_score,
            0 AS round_number,
            10 AS max_rounds,
            NULL AS created_at,
            NULL AS updated_at,
            {agent_alias}.company_id
        FROM galileo_agents {agent_alias}
    """


def negotiation_company_id(vendor_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", vendor_name.lower()).strip("-")
    if not normalized:
        normalized = "vendor"
    digest = hashlib.sha1(vendor_name.encode("utf-8")).hexdigest()[:10]
    return f"neg-company-{normalized[:40]}-{digest}"


def ensure_negotiation_scaffold(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO galileo_enterprises (id, name, description)
        VALUES (?, ?, ?)
        """,
        (
            NEGOTIATION_ENTERPRISE_ID,
            "Standalone Negotiations",
            "System-owned Galileo enterprise for non-event negotiation flows.",
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO galileo_events (
            id, enterprise_id, name, location, start_date, end_date, attendees, service, status, requirements, budget_per_person
        )
        VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, NULL)
        """,
        (
            NEGOTIATION_EVENT_ID,
            NEGOTIATION_ENTERPRISE_ID,
            "Standalone Negotiations",
            "N/A",
            "Hotel",
            "Active",
            "Synthetic Galileo event backing generic negotiation flows.",
        ),
    )


def ensure_vendor_company(conn: sqlite3.Connection, vendor_name: str) -> str:
    company_id = negotiation_company_id(vendor_name)
    conn.execute(
        """
        INSERT OR IGNORE INTO galileo_companies (id, name, initials, description, phone, website, industry, badge)
        VALUES (?, ?, ?, ?, NULL, NULL, ?, ?)
        """,
        (
            company_id,
            vendor_name,
            "".join(part[:1].upper() for part in vendor_name.split()[:3]) or "NV",
            "Synthetic vendor company created for standalone negotiation flows.",
            "Negotiation",
            "Standalone",
        ),
    )
    return company_id

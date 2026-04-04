import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.cache import set_add, set_members, set_remove
from app.core.database import get_db
from app.models.enums import CampaignJobStatus, CampaignStatus, NegotiationStatus
from app.models.schemas import BuyerConfig, CreateCampaignRequest, WorkerEvent
from app.services.events import publish_worker_event
from app.services.negotiation import create_negotiation_record
from app.services.scoring import compute_campaign_job_priority
from app.services.voice import initiate_call

_campaign_tasks: dict[str, asyncio.Task] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_campaign(row) -> dict:
    conn = get_db()
    active_workers = conn.execute(
        "SELECT COUNT(*) AS count FROM campaign_jobs WHERE campaign_id = ? AND status = ?",
        (row["id"], CampaignJobStatus.ACTIVE.value),
    ).fetchone()["count"]
    total_jobs = conn.execute(
        "SELECT COUNT(*) AS count FROM campaign_jobs WHERE campaign_id = ?",
        (row["id"],),
    ).fetchone()["count"]
    conn.close()
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "total_jobs": total_jobs,
        "active_workers": active_workers,
        "completed_jobs": row["completed_jobs"],
        "failed_jobs": row["failed_jobs"],
        "max_concurrent_workers": row["max_concurrent_workers"],
        "max_budget": row["max_budget"],
    }


def create_campaign(request: CreateCampaignRequest) -> dict:
    campaign_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO campaigns (
            id, name, status, max_concurrent_workers, max_budget, total_target_jobs, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            campaign_id,
            request.name,
            CampaignStatus.PENDING.value,
            request.max_concurrent_workers,
            request.max_budget,
            len(request.targets),
            now,
        ),
    )
    for target in request.targets:
        config = BuyerConfig.model_validate(target.config)
        negotiation = create_negotiation_record(
            vendor_name=target.vendor_name,
            strategy="balanced",
            config=config,
            product_category=target.product_category,
            campaign_id=campaign_id,
        )
        priority_score = compute_campaign_job_priority(
            config=config,
            priority=target.priority,
            deadline=target.deadline,
        )
        conn.execute(
            """
            INSERT INTO campaign_jobs (
                id, campaign_id, negotiation_id, vendor_name, vendor_phone, product_category,
                target_config, priority_score, deadline, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                campaign_id,
                negotiation["id"],
                target.vendor_name,
                target.vendor_phone,
                target.product_category,
                config.model_dump_json(),
                priority_score,
                target.deadline.isoformat() if target.deadline else None,
                CampaignJobStatus.QUEUED.value,
                now,
            ),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()
    return _serialize_campaign(row)


def list_campaigns() -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM campaigns ORDER BY updated_at DESC, created_at DESC"
    ).fetchall()
    conn.close()
    return [_serialize_campaign(row) for row in rows]


def get_campaign(campaign_id: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return _serialize_campaign(row)


def list_campaign_jobs(campaign_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, negotiation_id, vendor_name, vendor_phone, product_category, priority_score,
               status, retry_count, max_retries, deferred_until, failure_reason, updated_at
        FROM campaign_jobs
        WHERE campaign_id = ?
        ORDER BY priority_score DESC, created_at ASC
        """,
        (campaign_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _get_next_jobs(conn, campaign_id: str, limit: int) -> list:
    return conn.execute(
        """
        SELECT *
        FROM campaign_jobs
        WHERE campaign_id = ?
          AND status IN (?, ?)
          AND (deferred_until IS NULL OR deferred_until <= ?)
        ORDER BY priority_score DESC, created_at ASC
        LIMIT ?
        """,
        (
            campaign_id,
            CampaignJobStatus.QUEUED.value,
            CampaignJobStatus.RETRY.value,
            _utcnow(),
            limit,
        ),
    ).fetchall()


async def _launch_job(conn, campaign_row, job_row) -> None:
    now = _utcnow()
    conn.execute(
        """
        UPDATE campaign_jobs
        SET status = ?, updated_at = ?
        WHERE id = ?
        """,
        (CampaignJobStatus.ACTIVE.value, now, job_row["id"]),
    )
    conn.execute(
        """
        UPDATE negotiations
        SET worker_status = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        ("calling", NegotiationStatus.ACTIVE.value, now, job_row["negotiation_id"]),
    )
    conn.commit()
    await set_add(f"workers:active:{campaign_row['id']}", job_row["negotiation_id"])
    await publish_worker_event(
        campaign_row["id"],
        WorkerEvent(
            event_type="call_started",
            negotiation_id=job_row["negotiation_id"],
            campaign_id=campaign_row["id"],
            data={"vendor_name": job_row["vendor_name"], "job_id": job_row["id"]},
            timestamp=datetime.now(timezone.utc),
        ),
    )
    if job_row["vendor_phone"]:
        await asyncio.to_thread(
            initiate_call,
            job_row["negotiation_id"],
            job_row["vendor_phone"],
        )


async def run_campaign_loop(campaign_id: str) -> None:
    while True:
        conn = get_db()
        campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if campaign is None:
            conn.close()
            return
        if campaign["status"] == CampaignStatus.PAUSED.value:
            conn.close()
            await asyncio.sleep(1)
            continue
        active_workers = await set_members(f"workers:active:{campaign_id}")
        available_slots = max(0, campaign["max_concurrent_workers"] - len(active_workers))
        if available_slots:
            for job in _get_next_jobs(conn, campaign_id, available_slots):
                await _launch_job(conn, campaign, job)

        pending = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM campaign_jobs
            WHERE campaign_id = ? AND status IN (?, ?, ?)
            """,
            (
                campaign_id,
                CampaignJobStatus.QUEUED.value,
                CampaignJobStatus.RETRY.value,
                CampaignJobStatus.ACTIVE.value,
            ),
        ).fetchone()["count"]
        if pending == 0:
            conn.execute(
                "UPDATE campaigns SET status = ?, updated_at = ? WHERE id = ?",
                (CampaignStatus.COMPLETED.value, _utcnow(), campaign_id),
            )
            conn.commit()
            conn.close()
            return
        conn.close()
        await asyncio.sleep(1)


async def start_campaign(campaign_id: str) -> dict:
    conn = get_db()
    campaign = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if campaign is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found")
    conn.execute(
        "UPDATE campaigns SET status = ?, updated_at = ? WHERE id = ?",
        (CampaignStatus.RUNNING.value, _utcnow(), campaign_id),
    )
    conn.commit()
    conn.close()
    if campaign_id not in _campaign_tasks or _campaign_tasks[campaign_id].done():
        _campaign_tasks[campaign_id] = asyncio.create_task(run_campaign_loop(campaign_id))
    return get_campaign(campaign_id)


def pause_campaign(campaign_id: str) -> dict:
    conn = get_db()
    exists = conn.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if exists is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found")
    conn.execute(
        "UPDATE campaigns SET status = ?, updated_at = ? WHERE id = ?",
        (CampaignStatus.PAUSED.value, _utcnow(), campaign_id),
    )
    conn.commit()
    conn.close()
    return get_campaign(campaign_id)


async def resume_campaign(campaign_id: str) -> dict:
    return await start_campaign(campaign_id)


async def handle_job_completion(
    campaign_id: str,
    negotiation_id: str,
    success: bool,
    outcome: dict | None = None,
) -> None:
    conn = get_db()
    job = conn.execute(
        """
        SELECT * FROM campaign_jobs
        WHERE campaign_id = ? AND negotiation_id = ?
        """,
        (campaign_id, negotiation_id),
    ).fetchone()
    if job is None:
        conn.close()
        return
    status = CampaignJobStatus.COMPLETED.value if success else CampaignJobStatus.FAILED.value
    conn.execute(
        "UPDATE campaign_jobs SET status = ?, updated_at = ?, failure_reason = ? WHERE id = ?",
        (
            status,
            _utcnow(),
            None if success else (outcome or {}).get("failure_reason"),
            job["id"],
        ),
    )
    field = "completed_jobs" if success else "failed_jobs"
    conn.execute(
        f"UPDATE campaigns SET {field} = {field} + 1, updated_at = ? WHERE id = ?",
        (_utcnow(), campaign_id),
    )
    conn.commit()
    conn.close()
    await set_remove(f"workers:active:{campaign_id}", negotiation_id)


def emit_campaign_summary(campaign_id: str) -> dict:
    conn = get_db()
    exists = conn.execute("SELECT 1 FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if exists is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Campaign not found")
    best_quotes = conn.execute(
        """
        SELECT vendor_name, product_category, unit_price, shipping_cost, payment_terms_days, delivery_days,
               confidence_score, quote_timestamp, negotiation_id
        FROM latest_quotes
        WHERE negotiation_id IN (
            SELECT negotiation_id FROM campaign_jobs WHERE campaign_id = ?
        )
        ORDER BY unit_price ASC, confidence_score DESC
        LIMIT 10
        """,
        (campaign_id,),
    ).fetchall()
    pending = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM campaign_jobs
        WHERE campaign_id = ? AND status IN (?, ?, ?)
        """,
        (
            campaign_id,
            CampaignJobStatus.QUEUED.value,
            CampaignJobStatus.RETRY.value,
            CampaignJobStatus.ACTIVE.value,
        ),
    ).fetchone()["count"]
    retry_rows = conn.execute(
        """
        SELECT negotiation_id, vendor_name, deferred_until, retry_count
        FROM campaign_jobs
        WHERE campaign_id = ? AND status = ?
        ORDER BY deferred_until ASC
        """,
        (campaign_id, CampaignJobStatus.RETRY.value),
    ).fetchall()
    unreached = conn.execute(
        """
        SELECT vendor_name
        FROM campaign_jobs
        WHERE campaign_id = ? AND status IN (?, ?, ?)
        ORDER BY priority_score DESC
        """,
        (
            campaign_id,
            CampaignJobStatus.QUEUED.value,
            CampaignJobStatus.RETRY.value,
            CampaignJobStatus.DEFERRED.value,
        ),
    ).fetchall()
    estimated_savings = 0.0
    for row in best_quotes:
        target = conn.execute(
            """
            SELECT target_config
            FROM campaign_jobs
            WHERE negotiation_id = ?
            """,
            (row["negotiation_id"],),
        ).fetchone()
        if target is None:
            continue
        config = json.loads(target["target_config"])
        estimated_savings += max(0.0, (config["target_unit_price"] - row["unit_price"]) * config["quantity"])
    conn.close()
    return {
        "campaign_id": campaign_id,
        "best_quotes": [dict(row) for row in best_quotes],
        "pending_negotiations": pending,
        "retry_schedules": [dict(row) for row in retry_rows],
        "unreached_vendors": [row["vendor_name"] for row in unreached],
        "estimated_savings": round(estimated_savings, 2),
    }

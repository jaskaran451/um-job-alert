#!/usr/bin/env python3
"""Run one Railway-scheduled scrape and Telegram dispatch, then exit."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from app import app
from delivery_service import dispatch_to_subscribers
from models import MonitorState, PortalJob, utc_now
from telegram_service import telegram_sending_is_configured
from um_job_alert import (
    DEFAULT_MAX_ALERT_AGE_DAYS,
    DEFAULT_SOURCE_URLS,
    Job,
    csv_env,
    is_recent_enough_for_alert,
    local_today,
    scrape_jobs,
)


LOGGER = logging.getLogger("railway_cron")
BASE_DIR = Path(__file__).resolve().parent
LEGACY_STATE_FILE = BASE_DIR / "data" / "seen_jobs.json"


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_posting_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%b/%d/%Y").date()
    except ValueError:
        return None


def job_payload(job: Job | PortalJob) -> dict[str, str]:
    if isinstance(job, Job):
        return {
            "id": job.requisition_id,
            "title": job.title,
            "posting_date": job.posting_date,
            "url": job.detail_url,
        }
    return {
        "id": job.job_id,
        "title": job.title,
        "posting_date": job.posting_date,
        "url": job.url,
    }


def import_legacy_state(engine, path: Path = LEGACY_STATE_FILE) -> int:
    """Import the GitHub JSON baseline once so existing jobs are not resent."""
    with Session(engine) as session:
        if session.get(MonitorState, 1) is not None:
            return 0

        state_row = MonitorState(id=1, initialized=False)
        session.add(state_row)

        if not path.exists():
            session.commit()
            return 0

        try:
            legacy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Could not import legacy state %s: %s", path, exc)
            session.commit()
            return 0

        if not isinstance(legacy, dict):
            session.commit()
            return 0

        full_records: dict[str, dict[str, str]] = {}
        pending_ids: set[str] = set()
        for field in ("seen_jobs", "pending_jobs"):
            items = legacy.get(field, [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                job_id = str(item["id"])
                full_records[job_id] = {
                    "title": str(item.get("title", ""))[:240],
                    "posting_date": str(item.get("posting_date", ""))[:40],
                    "url": str(item.get("url", ""))[:500],
                }
                if field == "pending_jobs":
                    pending_ids.add(job_id)

        seen_ids = legacy.get("seen_ids", [])
        if not isinstance(seen_ids, list):
            seen_ids = []
        all_ids = {
            str(item).strip()
            for item in seen_ids
            if str(item).strip()
        }
        all_ids.update(full_records)

        imported_at = (
            parse_iso_datetime(legacy.get("last_success_utc")) or utc_now()
        )
        imported = 0
        for job_id in sorted(all_ids):
            record = full_records.get(job_id, {})
            has_dispatch_data = bool(record.get("title") and record.get("url"))
            session.add(
                PortalJob(
                    job_id=job_id,
                    title=record.get("title", ""),
                    posting_date=record.get("posting_date", ""),
                    posted_on=parse_posting_date(
                        record.get("posting_date", "")
                    ),
                    url=record.get("url", ""),
                    first_seen_at=imported_at,
                    last_seen_at=imported_at,
                    pending_delivery=(
                        job_id in pending_ids and has_dispatch_data
                    ),
                    suppressed_old=False,
                )
            )
            imported += 1

        state_row.initialized = bool(legacy.get("initialized", False))
        state_row.last_success_at = parse_iso_datetime(
            legacy.get("last_success_utc")
        )
        state_row.last_dispatch_at = parse_iso_datetime(
            legacy.get("last_dispatch_utc")
        )
        state_row.last_job_count = len(
            legacy.get("seen_jobs", [])
            if isinstance(legacy.get("seen_jobs"), list)
            else []
        )
        session.commit()
        return imported


def persist_scrape(
    engine,
    jobs: Sequence[Job],
    *,
    reference_date: date,
    max_alert_age_days: int,
) -> tuple[list[Job], list[Job], list[Job]]:
    """Upsert the portal snapshot and queue only genuinely new recent jobs."""
    now = utc_now()
    job_ids = [job.requisition_id for job in jobs]

    with Session(engine) as session:
        state = session.get(MonitorState, 1)
        if state is None:
            state = MonitorState(id=1, initialized=False)
            session.add(state)
            session.flush()

        existing = {
            row.job_id: row
            for row in session.scalars(
                select(PortalJob).where(PortalJob.job_id.in_(job_ids))
            )
        }
        first_run = not state.initialized
        unseen_jobs: list[Job] = []
        fresh_jobs: list[Job] = []
        suppressed_jobs: list[Job] = []

        for job in jobs:
            row = existing.get(job.requisition_id)
            if row is not None:
                row.title = job.title[:240]
                row.posting_date = job.posting_date[:40]
                row.posted_on = parse_posting_date(job.posting_date)
                row.url = job.detail_url[:500]
                row.last_seen_at = now
                continue

            unseen_jobs.append(job)
            recent = is_recent_enough_for_alert(
                job,
                reference_date,
                max_alert_age_days,
            )
            should_queue = not first_run and recent
            if should_queue:
                fresh_jobs.append(job)
            elif not first_run:
                suppressed_jobs.append(job)

            session.add(
                PortalJob(
                    job_id=job.requisition_id,
                    title=job.title[:240],
                    posting_date=job.posting_date[:40],
                    posted_on=parse_posting_date(job.posting_date),
                    url=job.detail_url[:500],
                    first_seen_at=now,
                    last_seen_at=now,
                    pending_delivery=should_queue,
                    suppressed_old=(not first_run and not recent),
                )
            )

        state.initialized = True
        state.last_success_at = now
        state.last_job_count = len(jobs)
        session.commit()

    return unseen_jobs, fresh_jobs, suppressed_jobs


def load_pending_jobs(engine) -> list[dict[str, str]]:
    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(PortalJob)
                .where(
                    PortalJob.pending_delivery.is_(True),
                    PortalJob.title != "",
                    PortalJob.url != "",
                )
                .order_by(
                    desc(PortalJob.posted_on),
                    desc(PortalJob.job_id),
                )
            )
        )
    return [job_payload(row) for row in rows]


def mark_pending_delivered(engine, job_ids: Sequence[str]) -> None:
    if not job_ids:
        return
    now = utc_now()
    with Session(engine) as session:
        session.execute(
            update(PortalJob)
            .where(PortalJob.job_id.in_(list(job_ids)))
            .values(pending_delivery=False)
        )
        state = session.get(MonitorState, 1)
        if state is not None:
            state.last_dispatch_at = now
        session.commit()


def run_once(
    *,
    scrape_func: Callable[..., list[Job]] = scrape_jobs,
    dispatch_func: Callable[..., dict[str, Any]] = dispatch_to_subscribers,
) -> dict[str, Any]:
    started = time.monotonic()
    engine = app.extensions["database_engine"]
    LOGGER.info("CRON START | database=%s", engine.url.get_backend_name())

    imported = import_legacy_state(engine)
    if imported:
        LOGGER.info("Imported %d legacy seen requisitions into PostgreSQL.", imported)

    os.environ.setdefault("BROWSER_CHANNEL", "")
    source_urls = csv_env("UM_JOB_URLS", DEFAULT_SOURCE_URLS)
    timeout_ms = int(os.getenv("PAGE_TIMEOUT_MS", "45000"))
    max_alert_age_days = int(
        os.getenv("MAX_ALERT_AGE_DAYS", str(DEFAULT_MAX_ALERT_AGE_DAYS))
    )

    scrape_started = time.monotonic()
    LOGGER.info("SCRAPE START | sources=%s | timeout_ms=%d", source_urls, timeout_ms)
    jobs = scrape_func(source_urls, timeout_ms=timeout_ms)
    LOGGER.info(
        "SCRAPE COMPLETE | jobs=%d | elapsed=%.1fs",
        len(jobs),
        time.monotonic() - scrape_started,
    )
    if not jobs:
        raise RuntimeError("The monitor received no jobs from the recruitment portal.")

    unseen, fresh, suppressed = persist_scrape(
        engine,
        jobs,
        reference_date=local_today(),
        max_alert_age_days=max_alert_age_days,
    )
    LOGGER.info(
        "STATE SAVED | total=%d unseen=%d fresh=%d suppressed_old=%d",
        len(jobs),
        len(unseen),
        len(fresh),
        len(suppressed),
    )
    for job in fresh:
        LOGGER.info("NEW %s | %s", job.requisition_id, job.title)
    for job in suppressed:
        LOGGER.warning(
            "SUPPRESSED OLD %s | %s | posted %s",
            job.requisition_id,
            job.title,
            job.posting_date,
        )

    pending = load_pending_jobs(engine)
    LOGGER.info("DISPATCH START | pending_jobs=%d", len(pending))
    if not pending:
        LOGGER.info("CRON COMPLETE | no pending jobs | elapsed=%.1fs", time.monotonic() - started)
        return {
            "received_jobs": 0,
            "matching_subscribers": 0,
            "telegram_attempted": 0,
            "telegram_delivered": 0,
            "failed": [],
        }

    if not telegram_sending_is_configured(app):
        raise RuntimeError(
            f"TELEGRAM_BOT_TOKEN is missing; keeping {len(pending)} jobs pending."
        )

    dispatch_started = time.monotonic()
    result = dispatch_func(app, engine, pending)
    LOGGER.info(
        "DISPATCH COMPLETE | elapsed=%.1fs | result=%s",
        time.monotonic() - dispatch_started,
        json.dumps(result, sort_keys=True),
    )
    if result.get("failed"):
        raise RuntimeError(
            f"Telegram dispatch was incomplete; keeping {len(pending)} jobs pending."
        )

    mark_pending_delivered(
        engine,
        [job["id"] for job in pending if job.get("id")],
    )
    LOGGER.info(
        "CRON COMPLETE | delivered_pending=%d | elapsed=%.1fs",
        len(pending),
        time.monotonic() - started,
    )
    return result


def main() -> int:
    if not os.getenv("DATABASE_URL", "").strip():
        raise RuntimeError(
            "DATABASE_URL is required for the Railway cron service."
        )

    engine = app.extensions["database_engine"]
    try:
        run_once()
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    try:
        raise SystemExit(main())
    except Exception as error:
        LOGGER.exception("Railway cron run failed: %s", error)
        raise SystemExit(1)

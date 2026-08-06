#!/usr/bin/env python3
"""Monitor the public University of Manitoba recruitment portal."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import tempfile
import urllib.parse

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger("um_job_alert")

DEFAULT_SOURCE_URLS = (
    "https://viprecprod.ad.umanitoba.ca/default",
)
MAX_PAGES_PER_SOURCE = 5
LATEST_JOBS_LIMIT = 40
MAX_SEEN_IDS = 5_000
DEFAULT_MAX_ALERT_AGE_DAYS = 14
LOCAL_TIMEZONE = ZoneInfo("America/Winnipeg")
SOURCE_NAME = "University of Manitoba recruitment portal"

REQUISITION_PATTERN = re.compile(
    r"Requisition\s+No:\s*(?P<id>\d+)\s*-\s*Category:\s*(?P<category>.+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Job:
    requisition_id: str
    title: str
    category: str
    job_type: str
    location: str
    posting_date: str
    source: str
    source_url: str
    detail_url: str

    @property
    def searchable_text(self) -> str:
        return " | ".join(
            (
                self.title,
                self.category,
                self.job_type,
                self.location,
                self.source,
            )
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def local_today() -> date:
    return datetime.now(LOCAL_TIMEZONE).date()


def csv_env(name: str, default: Sequence[str] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return tuple(default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def source_name(_url: str) -> str:
    return SOURCE_NAME


def detail_url(requisition_id: str) -> str:
    query = urllib.parse.urlencode({"REQ_ID": requisition_id, "Language": "1"})
    return f"https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?{query}"


def parse_job_cells(cells: Sequence[str], source_url: str) -> Job | None:
    """Parse the visible text from one recruitment-portal result row."""
    if len(cells) < 4:
        return None

    first_cell = normalize_space(cells[0])
    match = REQUISITION_PATTERN.search(first_cell)
    if not match:
        return None

    title = first_cell[: match.start()].strip(" -")
    requisition_id = match.group("id")
    category = normalize_space(match.group("category"))

    return Job(
        requisition_id=requisition_id,
        title=title,
        category=category,
        job_type=normalize_space(cells[1]),
        location=normalize_space(cells[2]),
        posting_date=normalize_space(cells[3]),
        source=source_name(source_url),
        source_url=source_url,
        detail_url=detail_url(requisition_id),
    )


def extract_jobs(page: Page, source_url: str) -> list[Job]:
    rows = page.locator("table tbody tr[recno]")
    count = rows.count()
    jobs: list[Job] = []

    for index in range(count):
        row = rows.nth(index)
        cells = row.locator("td")
        cell_texts = [cells.nth(i).inner_text() for i in range(cells.count())]
        job = parse_job_cells(cell_texts, source_url)
        if job:
            jobs.append(job)

    if not jobs:
        title = page.title()
        visible_text = normalize_space(page.locator("body").inner_text())[:500]
        raise RuntimeError(
            f"No jobs could be parsed from {source_url}. "
            f"The site may have changed. Page title: {title!r}; text: {visible_text!r}"
        )

    return jobs


def find_page_link(page: Page, page_number: int):
    """Locate a numbered paginator link on the recruitment portal."""
    exact_page = re.compile(rf"^\s*{page_number}\s*$")
    candidates = (
        page.locator("a[id^='bdy_21_']", has_text=exact_page),
        page.locator("a", has_text=exact_page),
    )
    for candidate in candidates:
        if candidate.count() > 0:
            return candidate.first
    return None


def scrape_source(context, source_url: str, timeout_ms: int) -> list[Job]:
    last_error: Exception | None = None

    for attempt in range(1, 3):
        page = context.new_page()

        try:
            LOGGER.info("Checking %s (attempt %d/2)", source_url, attempt)
            page.goto(
                source_url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            page.wait_for_selector(
                "table tbody tr[recno]",
                state="attached",
                timeout=timeout_ms,
            )

            jobs_by_id: dict[str, Job] = {}

            for page_number in range(1, MAX_PAGES_PER_SOURCE + 1):
                page_jobs = extract_jobs(page, source_url)
                for job in page_jobs:
                    jobs_by_id[job.requisition_id] = job

                next_page_number = page_number + 1
                if next_page_number > MAX_PAGES_PER_SOURCE:
                    break

                next_button = find_page_link(page, next_page_number)
                if next_button is None:
                    break

                previous_first_id = page_jobs[0].requisition_id
                next_button.click()
                page.wait_for_function(
                    """
                    previousId => {
                        const firstRow = document.querySelector(
                            "table tbody tr[recno]"
                        );
                        if (!firstRow) return false;
                        return !firstRow.innerText.includes(
                            `Requisition No: ${previousId}`
                        );
                    }
                    """,
                    arg=previous_first_id,
                    timeout=timeout_ms,
                )

            return list(jobs_by_id.values())

        except (PlaywrightTimeoutError, PlaywrightError, RuntimeError) as exc:
            last_error = exc
            LOGGER.warning(
                "Attempt %d failed for %s: %s",
                attempt,
                source_url,
                exc,
            )
        finally:
            page.close()

    raise RuntimeError(
        f"Could not read {source_url} after two attempts: {last_error}"
    )


def posting_sort_key(job: Job) -> tuple[datetime, int]:
    try:
        posting_date = datetime.strptime(job.posting_date, "%b/%d/%Y")
    except ValueError:
        posting_date = datetime.min

    try:
        requisition_number = int(job.requisition_id)
    except ValueError:
        requisition_number = 0

    return posting_date, requisition_number


def scrape_jobs(
    source_urls: Sequence[str],
    timeout_ms: int = 120_000,
) -> list[Job]:
    jobs_by_id: dict[str, Job] = {}

    with sync_playwright() as playwright:
        launch_options = {
            "headless": True,
            "args": ["--disable-dev-shm-usage", "--no-sandbox"],
        }
        browser_channel = os.getenv("BROWSER_CHANNEL", "chrome").strip()

        try:
            browser = playwright.chromium.launch(
                channel=browser_channel or None,
                **launch_options,
            )
        except PlaywrightError as exc:
            LOGGER.warning(
                "Chrome channel %r was unavailable (%s); trying Playwright Chromium.",
                browser_channel,
                exc,
            )
            browser = playwright.chromium.launch(**launch_options)

        context = browser.new_context(
            locale="en-CA",
            timezone_id="America/Winnipeg",
            viewport={"width": 1440, "height": 1000},
        )

        try:
            for source_url in source_urls:
                for job in scrape_source(context, source_url, timeout_ms):
                    jobs_by_id[job.requisition_id] = job
        finally:
            context.close()
            browser.close()

    return sorted(
        jobs_by_id.values(),
        key=posting_sort_key,
        reverse=True,
    )


def default_state() -> dict:
    return {
        "version": 3,
        "initialized": False,
        "seen_ids": [],
        "seen_jobs": [],
        "pending_jobs": [],
    }


def load_state(path: Path) -> dict:
    if not path.exists():
        return default_state()

    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Could not read state file {path}: {exc}") from exc

    if not isinstance(state, dict):
        raise RuntimeError(f"State file {path} must contain a JSON object.")

    for field in ("seen_ids", "seen_jobs", "pending_jobs"):
        if field in state and not isinstance(state[field], list):
            raise RuntimeError(f"State file {path} has an invalid {field} field.")

    state.setdefault("initialized", False)
    state.setdefault("seen_jobs", [])
    state.setdefault("pending_jobs", [])

    seen_ids = [str(item) for item in state.get("seen_ids", []) if str(item)]
    for collection in (state["seen_jobs"], state["pending_jobs"]):
        for item in collection:
            if isinstance(item, dict) and item.get("id"):
                seen_ids.append(str(item["id"]))

    state["seen_ids"] = deduplicate_ids(seen_ids)[-MAX_SEEN_IDS:]
    state["version"] = 3
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(state, indent=2, sort_keys=True) + "\n"

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(serialized)
        temporary_path = Path(temporary.name)

    temporary_path.replace(path)


def deduplicate_ids(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def job_record(job: Job) -> dict[str, str]:
    return {
        "id": job.requisition_id,
        "title": job.title,
        "posting_date": job.posting_date,
        "url": job.detail_url,
    }


def posting_age_days(job: Job, reference_date: date) -> int | None:
    try:
        posted = datetime.strptime(job.posting_date, "%b/%d/%Y").date()
    except ValueError:
        return None
    return (reference_date - posted).days


def is_recent_enough_for_alert(
    job: Job,
    reference_date: date,
    max_age_days: int,
) -> bool:
    age = posting_age_days(job, reference_date)
    return age is None or age <= max_age_days


def build_next_state(
    state: dict,
    jobs: Sequence[Job],
    *,
    reference_date: date,
    max_alert_age_days: int,
    success_time: str,
) -> tuple[dict, list[Job], list[Job], list[Job]]:
    """Return updated state and unseen/fresh/suppressed job groups."""
    normalized = dict(state)
    normalized.setdefault("seen_jobs", [])
    normalized.setdefault("pending_jobs", [])
    normalized.setdefault("seen_ids", [])

    known_ids = set(str(item) for item in normalized["seen_ids"])
    for collection in (normalized["seen_jobs"], normalized["pending_jobs"]):
        known_ids.update(
            str(item["id"])
            for item in collection
            if isinstance(item, dict) and item.get("id")
        )

    first_run = not normalized.get("initialized", False)
    unseen_jobs = [] if first_run else [
        job for job in jobs if job.requisition_id not in known_ids
    ]
    fresh_jobs = [
        job
        for job in unseen_jobs
        if is_recent_enough_for_alert(
            job,
            reference_date,
            max_alert_age_days,
        )
    ]
    fresh_ids = {job.requisition_id for job in fresh_jobs}
    suppressed_jobs = [
        job for job in unseen_jobs if job.requisition_id not in fresh_ids
    ]

    pending_by_id: dict[str, dict[str, str]] = {}
    for item in normalized["pending_jobs"]:
        if isinstance(item, dict) and item.get("id"):
            pending_by_id[str(item["id"])] = {
                "id": str(item["id"]),
                "title": str(item.get("title", "")),
                "posting_date": str(item.get("posting_date", "")),
                "url": str(item.get("url", "")),
            }
    for job in fresh_jobs:
        pending_by_id[job.requisition_id] = job_record(job)

    seen_ids = deduplicate_ids(
        [
            *[str(item) for item in normalized["seen_ids"]],
            *[
                str(item["id"])
                for item in normalized["seen_jobs"]
                if isinstance(item, dict) and item.get("id")
            ],
            *[job.requisition_id for job in jobs],
        ]
    )[-MAX_SEEN_IDS:]

    next_state = {
        "version": 3,
        "initialized": True,
        "last_success_utc": success_time,
        "seen_ids": seen_ids,
        "seen_jobs": [job_record(job) for job in jobs[:LATEST_JOBS_LIMIT]],
        "pending_jobs": list(pending_by_id.values()),
    }
    if normalized.get("last_dispatch_utc"):
        next_state["last_dispatch_utc"] = normalized["last_dispatch_utc"]

    return next_state, unseen_jobs, fresh_jobs, suppressed_jobs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.getenv("STATE_FILE", "data/seen_jobs.json")),
        help="JSON file used to remember posting and delivery state.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print fresh unseen jobs without changing state.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_urls = csv_env("UM_JOB_URLS", DEFAULT_SOURCE_URLS)
    timeout_ms = int(os.getenv("PAGE_TIMEOUT_MS", "120000"))
    max_alert_age_days = int(
        os.getenv("MAX_ALERT_AGE_DAYS", str(DEFAULT_MAX_ALERT_AGE_DAYS))
    )

    jobs = scrape_jobs(source_urls, timeout_ms=timeout_ms)
    if not jobs:
        raise RuntimeError("The monitor received no jobs from the recruitment portal.")

    state = load_state(args.state_file)
    next_state, unseen_jobs, fresh_jobs, suppressed_jobs = build_next_state(
        state,
        jobs,
        reference_date=local_today(),
        max_alert_age_days=max_alert_age_days,
        success_time=utc_now(),
    )

    LOGGER.info(
        "Read %d jobs from /default; %d unseen, %d fresh for alert, "
        "%d suppressed as old, %d pending delivery.",
        len(jobs),
        len(unseen_jobs),
        len(fresh_jobs),
        len(suppressed_jobs),
        len(next_state["pending_jobs"]),
    )
    for job in fresh_jobs:
        LOGGER.info(
            "NEW %s | %s | %s",
            job.requisition_id,
            job.title,
            job.detail_url,
        )
    for job in suppressed_jobs:
        LOGGER.warning(
            "SUPPRESSED OLD %s | %s | posted %s",
            job.requisition_id,
            job.title,
            job.posting_date,
        )

    if args.dry_run:
        print(json.dumps([asdict(job) for job in fresh_jobs], indent=2))
        return 0

    save_state(args.state_file, next_state)

    if not state.get("initialized", False):
        LOGGER.info(
            "Saved the current portal as the initial baseline; no existing jobs were queued."
        )

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    try:
        raise SystemExit(main())
    except Exception as error:
        LOGGER.error("%s", error)
        raise SystemExit(1)

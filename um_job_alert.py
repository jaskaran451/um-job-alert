#!/usr/bin/env python3
"""Monitor public University of Manitoba job postings and send new-job alerts."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import urllib.parse

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGGER = logging.getLogger("um_job_alert")

DEFAULT_SOURCE_URLS = (
    "https://viprecprod.ad.umanitoba.ca/A",
    "https://viprecprod.ad.umanitoba.ca/B",
    "https://viprecprod.ad.umanitoba.ca/P",
    "https://viprecprod.ad.umanitoba.ca/S",
    "https://viprecprod.ad.umanitoba.ca/T",
)

MAX_PAGES_PER_SOURCE = 4
LATEST_JOBS_LIMIT = 40

SOURCE_NAMES = {
    "A": "Academic and research",
    "B": "Sessional and student academic",
    "P": "Professional and management",
    "S": "Support staff",
    "T": "Trades and services",
}

# These defaults focus on opportunities most likely to be useful to a
# Computer Engineering student. Set ALERT_ALL=true to receive every posting.


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


def csv_env(name: str, default: Sequence[str] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return tuple(default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def source_name(url: str) -> str:
    path_code = urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].upper()
    return SOURCE_NAMES.get(path_code, path_code or "UM Careers")


def detail_url(requisition_id: str) -> str:
    query = urllib.parse.urlencode({"REQ_ID": requisition_id, "Language": "1"})
    return f"https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?{query}"


def parse_job_cells(cells: Sequence[str], source_url: str) -> Job | None:
    """Parse the visible text from one UM Careers result row."""
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

                next_button = page.locator(
                    "a[id^='bdy_21_']",
                    has_text=re.compile(
                        rf"^\s*{next_page_number}\s*$"
                    ),
                )

                if next_button.count() == 0:
                    break

                previous_first_id = page_jobs[0].requisition_id

                next_button.first.click()

                page.wait_for_function(
                    """
                    previousId => {
                        const firstRow =
                            document.querySelector(
                                "table tbody tr[recno]"
                            );

                        if (!firstRow) {
                            return false;
                        }

                        return !firstRow.innerText.includes(
                            `Requisition No: ${previousId}`
                        );
                    }
                    """,
                    arg=previous_first_id,
                    timeout=timeout_ms,
                )

            return list(jobs_by_id.values())

        except (
            PlaywrightTimeoutError,
            PlaywrightError,
            RuntimeError,
        ) as exc:
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
        posting_date = datetime.strptime(
            job.posting_date,
            "%b/%d/%Y",
        )
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
            "args": [
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }

        browser_channel = os.getenv(
            "BROWSER_CHANNEL",
            "chrome",
        ).strip()

        try:
            browser = playwright.chromium.launch(
                channel=browser_channel or None,
                **launch_options,
            )
        except PlaywrightError as exc:
            LOGGER.warning(
                "Chrome channel %r was unavailable (%s); "
                "trying Playwright Chromium.",
                browser_channel,
                exc,
            )

            browser = playwright.chromium.launch(
                **launch_options
            )

        context = browser.new_context(
            locale="en-CA",
            timezone_id="America/Winnipeg",
            viewport={
                "width": 1440,
                "height": 1000,
            },
        )

        try:
            for source_url in source_urls:
                source_jobs = scrape_source(
                    context,
                    source_url,
                    timeout_ms,
                )

                for job in source_jobs:
                    jobs_by_id[job.requisition_id] = job
        finally:
            context.close()
            browser.close()

    sorted_jobs = sorted(
        jobs_by_id.values(),
        key=posting_sort_key,
        reverse=True,
    )

    return sorted_jobs[:LATEST_JOBS_LIMIT]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "version": 2,
            "initialized": False,
            "seen_jobs": [],
        }

    try:
        state = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"Could not read state file {path}: {exc}"
        ) from exc

    if (
        "seen_jobs" in state
        and not isinstance(state["seen_jobs"], list)
    ):
        raise RuntimeError(
            f"State file {path} has an invalid "
            "seen_jobs field."
        )

    if (
        "seen_ids" in state
        and not isinstance(state["seen_ids"], list)
    ):
        raise RuntimeError(
            f"State file {path} has an invalid "
            "seen_ids field."
        )

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.getenv("STATE_FILE", "data/seen_jobs.json")),
        help="JSON file used to remember previously seen requisition IDs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print current matches without sending alerts or changing state.",
    )
    return parser

def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    source_urls = csv_env(
        "UM_JOB_URLS",
        DEFAULT_SOURCE_URLS,
    )

    timeout_ms = int(
        os.getenv("PAGE_TIMEOUT_MS", "120000")
    )

    jobs = scrape_jobs(
        source_urls,
        timeout_ms=timeout_ms,
    )

    if not jobs:
        raise RuntimeError(
            "The monitor received no jobs from "
            "any configured source."
        )

    state = load_state(args.state_file)

    stored_jobs = state.get("seen_jobs")
    migrating_old_state = not isinstance(
        stored_jobs,
        list,
    )

    if migrating_old_state:
        stored_jobs = []

    seen_ids = {
        str(item.get("id"))
        for item in stored_jobs
        if isinstance(item, dict) and item.get("id")
    }

    first_run = (
        not state.get("initialized", False)
        or migrating_old_state
    )

    if first_run:
        new_jobs: list[Job] = []
    else:
        new_jobs = [
            job
            for job in jobs
            if job.requisition_id not in seen_ids
        ]

    LOGGER.info(
        "Saved the latest %d jobs; found %d new jobs.",
        len(jobs),
        len(new_jobs),
    )

    for job in new_jobs:
        LOGGER.info(
            "NEW %s | %s | %s",
            job.requisition_id,
            job.title,
            job.detail_url,
        )

    if args.dry_run:
        print(
            json.dumps(
                [asdict(job) for job in new_jobs],
                indent=2,
            )
        )
        return 0

    new_state = {
        "version": 2,
        "initialized": True,
        "last_success_utc": utc_now(),
        "seen_jobs": [
            {
                "id": job.requisition_id,
                "title": job.title,
                "posting_date": job.posting_date,
                "url": job.detail_url,
            }
            for job in jobs
        ],
    }

    save_state(
        args.state_file,
        new_state,
    )

    if first_run:
        LOGGER.info(
            "The latest %d jobs were saved as the "
            "new baseline. No existing-job alerts "
            "were sent.",
            len(jobs),
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

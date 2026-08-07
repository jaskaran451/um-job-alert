import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import load_latest_jobs
from models import Base, MonitorState, PortalJob
from railway_cron import (
    import_legacy_state,
    load_pending_jobs,
    mark_pending_delivered,
    persist_scrape,
)
from um_job_alert import Job


def make_job(
    job_id: str,
    title: str = "Teaching Assistant - COMP 1010",
    posting_date: str = "Aug/06/2026",
) -> Job:
    return Job(
        requisition_id=job_id,
        title=title,
        category="CUPE STUDENTS",
        job_type="Part time",
        location="Fort Garry Campus",
        posting_date=posting_date,
        source="University of Manitoba recruitment portal",
        source_url="https://viprecprod.ad.umanitoba.ca/default",
        detail_url=(
            "https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?"
            f"REQ_ID={job_id}&Language=1"
        ),
    )


class RailwayCronStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Path(self.tmp.name) / "cron.db"
        self.engine = create_engine(f"sqlite:///{database}")
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_first_database_run_creates_baseline_without_alerting(self):
        baseline = make_job("50001")

        unseen, fresh, suppressed = persist_scrape(
            self.engine,
            [baseline],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )

        self.assertEqual([job.requisition_id for job in unseen], ["50001"])
        self.assertEqual(fresh, [])
        self.assertEqual(suppressed, [])
        self.assertEqual(load_pending_jobs(self.engine), [])

        with Session(self.engine) as session:
            state = session.get(MonitorState, 1)
            row = session.get(PortalJob, "50001")
            self.assertTrue(state.initialized)
            self.assertFalse(row.pending_delivery)

    def test_website_loader_reads_postgresql_snapshot(self):
        baseline = make_job("50001")
        persist_scrape(
            self.engine,
            [baseline],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )

        jobs, last_updated = load_latest_jobs(self.engine)

        self.assertEqual(jobs[0]["id"], "50001")
        self.assertEqual(jobs[0]["title"], baseline.title)
        self.assertEqual(jobs[0]["posting_date"], "Aug/06/2026")
        self.assertIsNotNone(last_updated)

    def test_new_recent_job_is_queued_after_baseline(self):
        baseline = make_job("50001")
        new_job = make_job("50002", title="Grader/Marker - COMP 1010")

        persist_scrape(
            self.engine,
            [baseline],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )
        _, fresh, _ = persist_scrape(
            self.engine,
            [new_job, baseline],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )

        self.assertEqual([job.requisition_id for job in fresh], ["50002"])
        self.assertEqual(
            [job["id"] for job in load_pending_jobs(self.engine)],
            ["50002"],
        )

    def test_old_unseen_job_is_stored_but_suppressed(self):
        persist_scrape(
            self.engine,
            [make_job("50001")],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )
        old_job = make_job(
            "46919",
            title="Light Equipment Operator",
            posting_date="Jun/18/2026",
        )

        _, fresh, suppressed = persist_scrape(
            self.engine,
            [make_job("50001"), old_job],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )

        self.assertEqual(fresh, [])
        self.assertEqual(
            [job.requisition_id for job in suppressed],
            ["46919"],
        )
        with Session(self.engine) as session:
            row = session.get(PortalJob, "46919")
            self.assertTrue(row.suppressed_old)
            self.assertFalse(row.pending_delivery)

    def test_legacy_json_history_is_imported_without_resending(self):
        state_path = Path(self.tmp.name) / "seen_jobs.json"
        state_path.write_text(
            json.dumps(
                {
                    "initialized": True,
                    "last_success_utc": "2026-08-06T15:00:00+00:00",
                    "seen_ids": ["48955"],
                    "seen_jobs": [
                        {
                            "id": "48955",
                            "title": "Teaching Assistant - ENG 1430 B01",
                            "posting_date": "Aug/05/2026",
                            "url": (
                                "https://viprecprod.ad.umanitoba.ca/"
                                "DEFAULT.ASPX?REQ_ID=48955&Language=1"
                            ),
                        }
                    ],
                    "pending_jobs": [],
                }
            ),
            encoding="utf-8",
        )

        imported = import_legacy_state(self.engine, state_path)
        unseen, fresh, suppressed = persist_scrape(
            self.engine,
            [make_job("48955", posting_date="Aug/05/2026")],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )

        self.assertEqual(imported, 1)
        self.assertEqual(unseen, [])
        self.assertEqual(fresh, [])
        self.assertEqual(suppressed, [])

    def test_pending_jobs_clear_only_when_marked_delivered(self):
        baseline = make_job("50001")
        queued = make_job("50002")
        persist_scrape(
            self.engine,
            [baseline],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )
        persist_scrape(
            self.engine,
            [queued, baseline],
            reference_date=date(2026, 8, 6),
            max_alert_age_days=14,
        )

        self.assertEqual(len(load_pending_jobs(self.engine)), 1)
        mark_pending_delivered(self.engine, ["50002"])
        self.assertEqual(load_pending_jobs(self.engine), [])

        with Session(self.engine) as session:
            row = session.scalar(
                select(PortalJob).where(PortalJob.job_id == "50002")
            )
            state = session.get(MonitorState, 1)
            self.assertFalse(row.pending_delivery)
            self.assertIsNotNone(state.last_dispatch_at)


if __name__ == "__main__":
    unittest.main()

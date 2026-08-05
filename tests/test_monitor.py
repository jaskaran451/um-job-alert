import tempfile
import unittest

from datetime import date
from pathlib import Path

from um_job_alert import (
    Job,
    build_next_state,
    detail_url,
    load_state,
    parse_job_cells,
    save_state,
)


def make_job(job_id: str, posting_date: str, title: str = "Teaching Assistant") -> Job:
    return Job(
        requisition_id=job_id,
        title=title,
        category="STUDENTS",
        job_type="Part Time - Temporary",
        location="Manitoba Fort Garry Campus [011]",
        posting_date=posting_date,
        source="University of Manitoba recruitment portal",
        source_url="https://viprecprod.ad.umanitoba.ca/default",
        detail_url=detail_url(job_id),
    )


class ParserTests(unittest.TestCase):
    def test_parse_default_portal_row_shape(self):
        job = parse_job_cells(
            [
                "TA/Demo/TUR/Sem. Leaders - COMP 1012 (CUPE Students)\n"
                "Requisition No: 48765 - Category: STUDENTS",
                "Part Time - Temporary",
                "Manitoba\nFort Garry Campus [011]",
                "Jul/29/2026",
                "",
            ],
            "https://viprecprod.ad.umanitoba.ca/default",
        )

        self.assertIsNotNone(job)
        self.assertEqual(job.requisition_id, "48765")
        self.assertEqual(
            job.title,
            "TA/Demo/TUR/Sem. Leaders - COMP 1012 (CUPE Students)",
        )
        self.assertEqual(job.category, "STUDENTS")
        self.assertEqual(job.location, "Manitoba Fort Garry Campus [011]")
        self.assertEqual(
            job.source,
            "University of Manitoba recruitment portal",
        )
        self.assertEqual(
            job.detail_url,
            "https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?"
            "REQ_ID=48765&Language=1",
        )

    def test_invalid_row_is_ignored(self):
        self.assertIsNone(
            parse_job_cells(
                ["not a posting", "Part Time", "Winnipeg", "Jul/29/2026"],
                "https://viprecprod.ad.umanitoba.ca/default",
            )
        )

    def test_detail_url_is_encoded(self):
        self.assertIn("REQ_ID=12345", detail_url("12345"))


class StateTests(unittest.TestCase):
    def test_missing_state_is_uninitialized(self):
        with tempfile.TemporaryDirectory() as directory:
            state = load_state(Path(directory) / "missing.json")
        self.assertFalse(state["initialized"])
        self.assertEqual(state["pending_jobs"], [])

    def test_legacy_seen_jobs_are_migrated_to_permanent_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(
                path,
                {
                    "version": 2,
                    "initialized": True,
                    "seen_jobs": [{"id": "48765", "title": "TA"}],
                },
            )
            state = load_state(path)
        self.assertEqual(state["version"], 3)
        self.assertIn("48765", state["seen_ids"])

    def test_old_resurfaced_job_is_seen_but_not_queued(self):
        state = {
            "version": 3,
            "initialized": True,
            "seen_ids": ["50000"],
            "seen_jobs": [],
            "pending_jobs": [],
        }
        old_job = make_job("46919", "Jun/18/2026", "Light Equipment Operator")
        next_state, unseen, fresh, suppressed = build_next_state(
            state,
            [old_job],
            reference_date=date(2026, 8, 5),
            max_alert_age_days=14,
            success_time="2026-08-05T18:00:00+00:00",
        )

        self.assertEqual([job.requisition_id for job in unseen], ["46919"])
        self.assertEqual(fresh, [])
        self.assertEqual([job.requisition_id for job in suppressed], ["46919"])
        self.assertIn("46919", next_state["seen_ids"])
        self.assertEqual(next_state["pending_jobs"], [])

    def test_fresh_job_is_added_to_retryable_pending_queue(self):
        state = {
            "version": 3,
            "initialized": True,
            "seen_ids": [],
            "seen_jobs": [],
            "pending_jobs": [
                {
                    "id": "48931",
                    "title": "GMGT 2070 A01 Sessional",
                    "posting_date": "Aug/05/2026",
                    "url": detail_url("48931"),
                }
            ],
        }
        new_job = make_job("48933", "Aug/05/2026", "EDUA 5620 Repost")
        next_state, _, fresh, _ = build_next_state(
            state,
            [new_job],
            reference_date=date(2026, 8, 5),
            max_alert_age_days=14,
            success_time="2026-08-05T18:00:00+00:00",
        )

        self.assertEqual([job.requisition_id for job in fresh], ["48933"])
        self.assertEqual(
            [job["id"] for job in next_state["pending_jobs"]],
            ["48931", "48933"],
        )

    def test_first_run_creates_baseline_without_queueing(self):
        state = {
            "version": 3,
            "initialized": False,
            "seen_ids": [],
            "seen_jobs": [],
            "pending_jobs": [],
        }
        next_state, unseen, fresh, suppressed = build_next_state(
            state,
            [make_job("48933", "Aug/05/2026")],
            reference_date=date(2026, 8, 5),
            max_alert_age_days=14,
            success_time="2026-08-05T18:00:00+00:00",
        )
        self.assertEqual(unseen, [])
        self.assertEqual(fresh, [])
        self.assertEqual(suppressed, [])
        self.assertEqual(next_state["pending_jobs"], [])
        self.assertIn("48933", next_state["seen_ids"])


if __name__ == "__main__":
    unittest.main()

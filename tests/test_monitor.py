import tempfile
import unittest
from pathlib import Path

from um_job_alert import (
    Job,
    detail_url,
    job_matches,
    keyword_matches,
    load_state,
    parse_job_cells,
    save_state,
)


class ParserTests(unittest.TestCase):
    def test_parse_live_row_shape(self):
        job = parse_job_cells(
            [
                "TA/Demo/TUR/Sem. Leaders - COMP 1012 (CUPE Students)\n"
                "Requisition No: 48765 - Category: STUDENTS",
                "Part Time - Temporary",
                "Manitoba\nFort Garry Campus [011]",
                "Jul/29/2026",
                "",
            ],
            "https://viprecprod.ad.umanitoba.ca/B",
        )

        self.assertIsNotNone(job)
        self.assertEqual(job.requisition_id, "48765")
        self.assertEqual(
            job.title,
            "TA/Demo/TUR/Sem. Leaders - COMP 1012 (CUPE Students)",
        )
        self.assertEqual(job.category, "STUDENTS")
        self.assertEqual(job.location, "Manitoba Fort Garry Campus [011]")
        self.assertEqual(job.source, "Sessional and student academic")
        self.assertEqual(
            job.detail_url,
            "https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?"
            "REQ_ID=48765&Language=1",
        )

    def test_invalid_row_is_ignored(self):
        self.assertIsNone(
            parse_job_cells(
                ["not a posting", "Part Time", "Winnipeg", "Jul/29/2026"],
                "https://viprecprod.ad.umanitoba.ca/B",
            )
        )

    def test_detail_url_is_encoded(self):
        self.assertIn("REQ_ID=12345", detail_url("12345"))


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.job = Job(
            requisition_id="48765",
            title="TA/Demo/TUR/Sem. Leaders - COMP 1012",
            category="STUDENTS",
            job_type="Part Time - Temporary",
            location="Manitoba Fort Garry Campus",
            posting_date="Jul/29/2026",
            source="Sessional and student academic",
            source_url="https://viprecprod.ad.umanitoba.ca/B",
            detail_url=detail_url("48765"),
        )

    def test_short_code_uses_word_boundaries(self):
        self.assertFalse(keyword_matches("Manitoba Temporary", "TA"))
        self.assertTrue(keyword_matches("TA/Demo position", "TA"))

    def test_included_keyword_matches(self):
        self.assertTrue(job_matches(self.job, ["COMP"], [], False))

    def test_exclusion_wins(self):
        self.assertFalse(job_matches(self.job, ["COMP"], ["1012"], False))

    def test_alert_all(self):
        self.assertTrue(job_matches(self.job, [], [], True))


class StateTests(unittest.TestCase):
    def test_missing_state_is_uninitialized(self):
        with tempfile.TemporaryDirectory() as directory:
            state = load_state(Path(directory) / "missing.json")
        self.assertFalse(state["initialized"])

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            expected = {
                "version": 1,
                "initialized": True,
                "seen_ids": ["1", "2"],
            }
            save_state(path, expected)
            actual = load_state(path)
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()

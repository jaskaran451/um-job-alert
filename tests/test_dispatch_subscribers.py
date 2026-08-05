import json
import os
import sys
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

import dispatch_subscribers


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"failed": [], "telegram_delivered": 1}).encode()


class DispatchStateTests(unittest.TestCase):
    def write_state(self, directory: str) -> Path:
        path = Path(directory) / "state.json"
        path.write_text(
            json.dumps(
                {
                    "version": 3,
                    "initialized": True,
                    "pending_jobs": [
                        {
                            "id": "48933",
                            "title": "Sessional Instructor",
                            "posting_date": "Aug/05/2026",
                            "url": (
                                "https://viprecprod.ad.umanitoba.ca/"
                                "DEFAULT.ASPX?REQ_ID=48933&Language=1"
                            ),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_success_clears_pending_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(directory)
            with (
                patch.dict(
                    os.environ,
                    {
                        "SUBSCRIBER_API_URL": "https://example.test",
                        "SUBSCRIBER_API_KEY": "secret",
                    },
                    clear=False,
                ),
                patch.object(sys, "argv", ["dispatch", "--state-file", str(path)]),
                patch(
                    "dispatch_subscribers.urllib.request.urlopen",
                    return_value=FakeResponse(),
                ),
            ):
                self.assertEqual(dispatch_subscribers.main(), 0)

            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(state["pending_jobs"], [])
            self.assertIn("last_dispatch_utc", state)

    def test_timeout_keeps_pending_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_state(directory)
            with (
                patch.dict(
                    os.environ,
                    {
                        "SUBSCRIBER_API_URL": "https://example.test",
                        "SUBSCRIBER_API_KEY": "secret",
                    },
                    clear=False,
                ),
                patch.object(sys, "argv", ["dispatch", "--state-file", str(path)]),
                patch(
                    "dispatch_subscribers.urllib.request.urlopen",
                    side_effect=TimeoutError("timed out"),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    dispatch_subscribers.main()

            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([job["id"] for job in state["pending_jobs"]], ["48933"])


if __name__ == "__main__":
    unittest.main()

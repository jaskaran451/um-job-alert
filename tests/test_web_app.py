import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import create_app


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Path(self.tmp.name) / "test.db"
        self.telegram_calls = []

        def fake_telegram(method, payload):
            self.telegram_calls.append((method, payload))
            return {"ok": True, "result": {"message_id": len(self.telegram_calls)}}

        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE_URL": f"sqlite:///{database}",
                "SECRET_KEY": "test-secret",
                "DISPATCH_API_KEY": "dispatch-secret",
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_BOT_USERNAME": "um_test_bot",
                "TELEGRAM_WEBHOOK_SECRET": "webhook-secret",
                "TELEGRAM_API_CALL": fake_telegram,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def create_subscription(self):
        return self.client.post(
            "/api/subscriptions",
            json={
                "email": "Student@Example.com",
                "role_types": ["teaching_assistant"],
                "keywords": ["COMP"],
                "consent": True,
            },
        )

    def connect_telegram(self, connect_url):
        token = parse_qs(urlparse(connect_url).query)["start"][0]
        return self.client.post(
            "/api/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "text": f"/start {token}",
                    "chat": {
                        "id": 12345,
                        "type": "private",
                        "username": "student",
                        "first_name": "Student",
                    },
                },
            },
        )

    def test_home_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create your job alert", response.data)
        self.assertIn(b"Telegram", response.data)

    def test_healthcheck_verifies_database(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["database"], "sqlite")

    def test_subscription_requires_consent(self):
        response = self.client.post(
            "/api/subscriptions",
            json={
                "email": "student@example.com",
                "role_types": ["teaching_assistant"],
                "keywords": [],
                "consent": False,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("consent", response.get_json()["errors"])

    def test_subscription_returns_private_telegram_link(self):
        response = self.create_subscription()
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["email"], "student@example.com")
        self.assertTrue(payload["telegram_available"])
        self.assertTrue(
            payload["telegram_connect_url"].startswith(
                "https://t.me/um_test_bot?start="
            )
        )

    def test_webhook_requires_secret(self):
        response = self.client.post("/api/telegram/webhook", json={})
        self.assertEqual(response.status_code, 401)

    def test_start_link_connects_chat_and_dispatches_telegram(self):
        subscription = self.create_subscription().get_json()
        response = self.connect_telegram(subscription["telegram_connect_url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.telegram_calls[-1][0], "sendMessage")
        self.assertIn("connected", self.telegram_calls[-1][1]["text"])

        dispatch = self.client.post(
            "/api/internal/dispatch",
            headers={"X-Dispatch-Key": "dispatch-secret"},
            json={
                "jobs": [
                    {
                        "id": "50001",
                        "title": "Teaching Assistant - COMP 1010",
                        "posting_date": "Aug/04/2026",
                        "url": "https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?REQ_ID=50001",
                    }
                ]
            },
        )
        self.assertEqual(dispatch.status_code, 200)
        payload = dispatch.get_json()
        self.assertEqual(payload["telegram_attempted"], 1)
        self.assertEqual(payload["telegram_delivered"], 1)

        second_dispatch = self.client.post(
            "/api/internal/dispatch",
            headers={"X-Dispatch-Key": "dispatch-secret"},
            json={
                "jobs": [
                    {
                        "id": "50001",
                        "title": "Teaching Assistant - COMP 1010",
                        "posting_date": "Aug/04/2026",
                        "url": "https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?REQ_ID=50001",
                    }
                ]
            },
        )
        self.assertEqual(second_dispatch.status_code, 200)
        self.assertEqual(second_dispatch.get_json()["telegram_attempted"], 0)

    def test_stop_disconnects_telegram(self):
        subscription = self.create_subscription().get_json()
        self.connect_telegram(subscription["telegram_connect_url"])
        response = self.client.post(
            "/api/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "webhook-secret"},
            json={
                "update_id": 2,
                "message": {
                    "message_id": 2,
                    "text": "/stop",
                    "chat": {"id": 12345, "type": "private"},
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("disconnected", self.telegram_calls[-1][1]["text"])

    def test_dispatch_requires_key(self):
        response = self.client.post(
            "/api/internal/dispatch",
            json={
                "jobs": [
                    {
                        "id": "1",
                        "title": "Teaching Assistant",
                        "url": "https://viprecprod.ad.umanitoba.ca/DEFAULT.ASPX?REQ_ID=1",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()

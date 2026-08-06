import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import create_app
from models import Delivery, Subscription, TelegramConnection, utc_now
from telegram_service import TelegramAPIError, format_telegram_alert


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        database = Path(self.tmp.name) / "test.db"
        self.telegram_calls = []

        def fake_telegram(method, payload):
            self.telegram_calls.append((method, payload))
            return {
                "ok": True,
                "result": {
                    "message_id": len(self.telegram_calls)
                },
            }

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
                "TELEGRAM_JOBS_PER_MESSAGE": 8,
                "TELEGRAM_MESSAGE_LIMIT": 3800,
            }
        )
        self.client = self.app.test_client()
        self.engine = self.app.extensions["database_engine"]

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def create_subscription(self, roles=None, keywords=None):
        return self.client.post(
            "/api/subscriptions",
            json={
                "role_types": roles or ["teaching_assistant"],
                "keywords": keywords or ["COMP"],
                "consent": True,
            },
        )

    def connect_telegram(self, connect_url, chat_id=12345):
        token = parse_qs(
            urlparse(connect_url).query
        )["start"][0]
        return self.client.post(
            "/api/telegram/webhook",
            headers={
                "X-Telegram-Bot-Api-Secret-Token": (
                    "webhook-secret"
                )
            },
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "text": f"/start {token}",
                    "chat": {
                        "id": chat_id,
                        "type": "private",
                        "username": "student",
                        "first_name": "Student",
                    },
                },
            },
        )

    @staticmethod
    def jobs(count):
        return [
            {
                "id": str(50000 + index),
                "title": f"Teaching Assistant - COMP {index}",
                "posting_date": "Aug/05/2026",
                "url": (
                    "https://viprecprod.ad.umanitoba.ca/"
                    f"DEFAULT.ASPX?REQ_ID={50000 + index}"
                ),
            }
            for index in range(1, count + 1)
        ]

    def dispatch(self, jobs):
        return self.client.post(
            "/api/internal/dispatch",
            headers={"X-Dispatch-Key": "dispatch-secret"},
            json={"jobs": jobs},
        )

    def test_home_loads_without_email_field(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Create your job alert", response.data)
        self.assertIn(b"Create Telegram alert", response.data)
        self.assertNotIn(b'type="email"', response.data)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(
            response.headers["X-Content-Type-Options"],
            "nosniff",
        )

    def test_healthcheck_verifies_database_and_telegram(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["database"], "sqlite")
        self.assertTrue(payload["telegram"])

    def test_subscription_requires_json(self):
        response = self.client.post(
            "/api/subscriptions",
            data="not json",
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 415)

    def test_subscription_requires_telegram_consent(self):
        response = self.client.post(
            "/api/subscriptions",
            json={
                "role_types": ["teaching_assistant"],
                "keywords": [],
                "consent": False,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("consent", response.get_json()["errors"])

    def test_subscription_is_inactive_until_telegram_connects(self):
        response = self.create_subscription()
        self.assertEqual(response.status_code, 201)

        with Session(self.engine) as session:
            subscription = session.scalar(select(Subscription))
            self.assertIsNotNone(subscription)
            self.assertFalse(subscription.active)

        dispatch = self.dispatch(self.jobs(1))
        self.assertEqual(dispatch.status_code, 200)
        self.assertEqual(dispatch.get_json()["telegram_attempted"], 0)

    def test_subscription_returns_private_telegram_link(self):
        response = self.create_subscription()
        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertNotIn("email", payload)
        self.assertTrue(payload["telegram_available"])
        self.assertTrue(
            payload["telegram_connect_url"].startswith(
                "https://t.me/um_test_bot?start="
            )
        )

        with Session(self.engine) as session:
            subscription = session.scalar(select(Subscription))
            self.assertIsNotNone(subscription)
            self.assertTrue(
                subscription.email.endswith("@alerts.invalid")
            )

    def test_expired_unconnected_subscription_is_cleaned_up(self):
        self.create_subscription()
        with Session(self.engine) as session:
            connection = session.scalar(select(TelegramConnection))
            connection.connect_expires_at = utc_now() - timedelta(minutes=1)
            session.commit()

        self.create_subscription(
            roles=["research"],
            keywords=["laboratory"],
        )

        with Session(self.engine) as session:
            subscription_count = session.scalar(
                select(func.count()).select_from(Subscription)
            )
            connection_count = session.scalar(
                select(func.count()).select_from(TelegramConnection)
            )

        self.assertEqual(subscription_count, 1)
        self.assertEqual(connection_count, 1)

    def test_webhook_requires_secret(self):
        response = self.client.post(
            "/api/telegram/webhook",
            json={},
        )
        self.assertEqual(response.status_code, 401)

    def test_start_link_connects_chat_and_dispatches_telegram(self):
        subscription = self.create_subscription().get_json()
        response = self.connect_telegram(
            subscription["telegram_connect_url"]
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.telegram_calls[-1][0], "sendMessage")
        self.assertIn(
            "connected",
            self.telegram_calls[-1][1]["text"],
        )

        dispatch = self.dispatch(self.jobs(1))
        self.assertEqual(dispatch.status_code, 200)
        payload = dispatch.get_json()
        self.assertNotIn("email_attempted", payload)
        self.assertEqual(payload["telegram_attempted"], 1)
        self.assertEqual(payload["telegram_delivered"], 1)
        self.assertEqual(payload["telegram_jobs_delivered"], 1)

        second_dispatch = self.dispatch(self.jobs(1))
        self.assertEqual(second_dispatch.status_code, 200)
        self.assertEqual(
            second_dispatch.get_json()["telegram_attempted"],
            0,
        )

    def test_partial_failure_records_successful_batch_before_retry(self):
        subscription = self.create_subscription(
            roles=["all"],
            keywords=[],
        ).get_json()
        self.connect_telegram(subscription["telegram_connect_url"])
        self.telegram_calls.clear()
        self.app.config["TELEGRAM_JOBS_PER_MESSAGE"] = 2

        attempts = 0

        def flaky_telegram(method, payload):
            nonlocal attempts
            attempts += 1
            self.telegram_calls.append((method, payload))
            if attempts == 2:
                raise TelegramAPIError("temporary Telegram failure")
            return {"ok": True, "result": {"message_id": attempts}}

        self.app.config["TELEGRAM_API_CALL"] = flaky_telegram
        first = self.dispatch(self.jobs(5))
        self.assertEqual(first.status_code, 207)
        self.assertEqual(first.get_json()["telegram_jobs_delivered"], 2)

        with Session(self.engine) as session:
            delivered_after_failure = session.scalar(
                select(func.count()).select_from(Delivery)
            )
        self.assertEqual(delivered_after_failure, 2)

        retry_calls = []

        def working_telegram(method, payload):
            retry_calls.append((method, payload))
            return {
                "ok": True,
                "result": {"message_id": len(retry_calls)},
            }

        self.app.config["TELEGRAM_API_CALL"] = working_telegram
        second = self.dispatch(self.jobs(5))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["telegram_jobs_delivered"], 3)

        retry_text = "\n".join(
            payload["text"] for _, payload in retry_calls
        )
        self.assertNotIn("50001", retry_text)
        self.assertNotIn("50002", retry_text)
        self.assertIn("50003", retry_text)
        self.assertIn("50005", retry_text)

        with Session(self.engine) as session:
            delivered_after_retry = session.scalar(
                select(func.count()).select_from(Delivery)
            )
        self.assertEqual(delivered_after_retry, 5)

    def test_alert_header_uses_correct_plural(self):
        singular = format_telegram_alert(self.jobs(1))
        plural = format_telegram_alert(self.jobs(2))
        self.assertIn("1 new U of M job match", singular)
        self.assertNotIn("job matchs", singular)
        self.assertIn("2 new U of M job matches", plural)

    def test_reconnecting_same_chat_replaces_old_preferences(self):
        first = self.create_subscription(
            roles=["teaching_assistant"],
            keywords=[],
        ).get_json()
        self.connect_telegram(first["telegram_connect_url"])

        second = self.create_subscription(
            roles=["research"],
            keywords=[],
        ).get_json()
        self.connect_telegram(second["telegram_connect_url"])

        with Session(self.engine) as session:
            active_count = session.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.active.is_(True))
            )
            connected_count = session.scalar(
                select(func.count())
                .select_from(TelegramConnection)
                .where(TelegramConnection.enabled.is_(True))
            )

        self.assertEqual(active_count, 1)
        self.assertEqual(connected_count, 1)

    def test_status_does_not_reference_email(self):
        subscription = self.create_subscription().get_json()
        self.connect_telegram(
            subscription["telegram_connect_url"]
        )

        response = self.client.post(
            "/api/telegram/webhook",
            headers={
                "X-Telegram-Bot-Api-Secret-Token": (
                    "webhook-secret"
                )
            },
            json={
                "update_id": 2,
                "message": {
                    "message_id": 2,
                    "text": "/status",
                    "chat": {
                        "id": 12345,
                        "type": "private",
                    },
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        reply = self.telegram_calls[-1][1]["text"]
        self.assertIn("active", reply)
        self.assertNotIn("email", reply.casefold())
        self.assertNotIn("@", reply)

    def test_stop_disables_entire_subscription(self):
        subscription = self.create_subscription().get_json()
        self.connect_telegram(
            subscription["telegram_connect_url"]
        )

        response = self.client.post(
            "/api/telegram/webhook",
            headers={
                "X-Telegram-Bot-Api-Secret-Token": (
                    "webhook-secret"
                )
            },
            json={
                "update_id": 3,
                "message": {
                    "message_id": 3,
                    "text": "/stop",
                    "chat": {
                        "id": 12345,
                        "type": "private",
                    },
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "disconnected",
            self.telegram_calls[-1][1]["text"],
        )

        with Session(self.engine) as session:
            subscription_row = session.scalar(
                select(Subscription)
            )
            self.assertFalse(subscription_row.active)

    def test_dispatch_requires_key(self):
        response = self.client.post(
            "/api/internal/dispatch",
            json={"jobs": self.jobs(1)},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()

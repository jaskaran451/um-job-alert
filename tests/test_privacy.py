import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Base, Subscription, TelegramConnection


class TelegramPrivacyTests(unittest.TestCase):
    def test_profile_name_fields_are_not_stored(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(
                f"sqlite:///{Path(directory) / 'privacy.db'}"
            )
            Base.metadata.create_all(engine)

            with Session(engine) as session:
                subscription = Subscription(
                    email="student@example.com",
                    active=False,
                )
                session.add(subscription)
                session.flush()
                session.add(
                    TelegramConnection(
                        subscription_id=subscription.id,
                        chat_id="12345",
                        username="public_username",
                        first_name="Student",
                        enabled=True,
                    )
                )
                session.commit()

            with Session(engine) as session:
                connection = session.scalar(select(TelegramConnection))
                self.assertIsNotNone(connection)
                self.assertEqual(connection.chat_id, "12345")
                self.assertIsNone(connection.username)
                self.assertIsNone(connection.first_name)

            engine.dispose()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from typing import Any, Iterable

from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Delivery, Subscription, TelegramConnection
from telegram_service import (
    TelegramAPIError,
    disable_broken_telegram_connection,
    send_job_batch_telegram,
    telegram_job_batches,
)


ROLE_KEYWORDS = {
    "teaching_assistant": (
        "teaching assistant",
        "ta/demo",
        "lab demonstrator",
        "tutor",
    ),
    "grader_marker": ("grader", "marker"),
    "instructor_sessionals": (
        "instructor",
        "lecturer",
        "sessional",
    ),
    "technical_it": (
        "technician",
        "technical",
        "analyst",
        "developer",
        "programmer",
        "engineer",
        "service desk",
        "information technology",
        "computer",
    ),
    "research": (
        "research assistant",
        "research associate",
        "research",
    ),
}


def subscription_matches(
    subscription: Subscription,
    job: dict[str, str],
) -> bool:
    title = job["title"].casefold()
    if "all" in subscription.role_types:
        return True

    role_match = any(
        any(
            keyword in title
            for keyword in ROLE_KEYWORDS.get(role, ())
        )
        for role in subscription.role_types
    )
    keyword_match = any(
        keyword.casefold() in title
        for keyword in subscription.keywords
    )
    return role_match or keyword_match


def dispatch_to_subscribers(
    app: Flask,
    engine,
    jobs: list[dict[str, str]],
) -> dict[str, Any]:
    job_ids = [job["id"] for job in jobs if job["id"]]

    with Session(engine) as session:
        subscriptions = list(
            session.scalars(
                select(Subscription).where(
                    Subscription.active.is_(True)
                )
            )
        )
        connections = {
            connection.subscription_id: connection
            for connection in session.scalars(
                select(TelegramConnection).where(
                    TelegramConnection.enabled.is_(True),
                    TelegramConnection.chat_id.is_not(None),
                )
            )
        }

        delivery_rows = []
        if job_ids:
            delivery_rows = list(
                session.execute(
                    select(
                        Delivery.subscription_id,
                        Delivery.job_id,
                        Delivery.channel,
                    ).where(Delivery.job_id.in_(job_ids))
                )
            )
        delivered_keys = {
            (subscription_id, job_id, channel)
            for subscription_id, job_id, channel in delivery_rows
        }

    result = {
        "received_jobs": len(jobs),
        "matching_subscribers": 0,
        "telegram_attempted": 0,
        "telegram_delivered": 0,
        "telegram_batches_delivered": 0,
        "telegram_jobs_delivered": 0,
        "failed": [],
    }

    for subscription in subscriptions:
        connection = connections.get(subscription.id)
        if connection is None or not connection.chat_id:
            continue

        matching_jobs = [
            job
            for job in jobs
            if subscription_matches(subscription, job)
        ]
        if not matching_jobs:
            continue

        telegram_jobs = [
            job
            for job in matching_jobs
            if (
                subscription.id,
                job["id"],
                "telegram",
            )
            not in delivered_keys
        ]
        if not telegram_jobs:
            continue

        result["matching_subscribers"] += 1
        result["telegram_attempted"] += 1
        subscription_succeeded = True

        for batch in telegram_job_batches(app, telegram_jobs):
            try:
                send_job_batch_telegram(
                    app,
                    connection.chat_id,
                    batch,
                )
                record_deliveries(
                    engine,
                    subscription.id,
                    batch,
                    "telegram",
                )
                result["telegram_batches_delivered"] += 1
                result["telegram_jobs_delivered"] += len(batch)
            except TelegramAPIError as exc:
                subscription_succeeded = False
                app.logger.exception("Could not deliver Telegram alert")
                if exc.error_code in {400, 403}:
                    disable_broken_telegram_connection(
                        engine,
                        connection.id,
                    )
                result["failed"].append(
                    f"telegram subscription {subscription.id}: {exc}"
                )
                break
            except Exception as exc:
                subscription_succeeded = False
                app.logger.exception("Could not deliver Telegram alert")
                result["failed"].append(
                    f"telegram subscription {subscription.id}: {exc}"
                )
                break

        if subscription_succeeded:
            result["telegram_delivered"] += 1

    return result


def record_deliveries(
    engine,
    subscription_id: int,
    jobs: Iterable[dict[str, str]],
    channel: str,
) -> None:
    with Session(engine) as session:
        for job in jobs:
            if not job["id"]:
                continue

            session.add(
                Delivery(
                    subscription_id=subscription_id,
                    job_id=job["id"],
                    channel=channel,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()

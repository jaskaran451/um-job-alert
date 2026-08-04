from __future__ import annotations

import hashlib
import html
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Iterable

from flask import Flask, request
from itsdangerous import URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Delivery, Subscription, TelegramConnection
from telegram_service import (
    TelegramAPIError,
    disable_broken_telegram_connection,
    send_job_telegram,
)


ROLE_KEYWORDS = {
    "teaching_assistant": ("teaching assistant", "ta/demo", "lab demonstrator", "tutor"),
    "grader_marker": ("grader", "marker"),
    "instructor_sessionals": ("instructor", "lecturer", "sessional"),
    "technical_it": (
        "technician", "technical", "analyst", "developer", "programmer",
        "engineer", "service desk", "information technology", "computer",
    ),
    "research": ("research assistant", "research associate", "research"),
}


def subscription_matches(subscription: Subscription, job: dict[str, str]) -> bool:
    title = job["title"].casefold()
    if "all" in subscription.role_types:
        return True
    role_match = any(
        any(keyword in title for keyword in ROLE_KEYWORDS.get(role, ()))
        for role in subscription.role_types
    )
    keyword_match = any(keyword.casefold() in title for keyword in subscription.keywords)
    return role_match or keyword_match


def smtp_is_configured() -> bool:
    return bool(
        os.getenv("SMTP_HOST", "").strip()
        and os.getenv("ALERT_EMAIL_FROM", "").strip()
    )


def dispatch_to_subscribers(
    app: Flask,
    engine,
    jobs: list[dict[str, str]],
    *,
    email_available: bool,
    telegram_available: bool,
) -> dict[str, Any]:
    job_ids = [job["id"] for job in jobs if job["id"]]
    with Session(engine) as session:
        subscriptions = list(
            session.scalars(select(Subscription).where(Subscription.active.is_(True)))
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
                    select(Delivery.subscription_id, Delivery.job_id, Delivery.channel)
                    .where(Delivery.job_id.in_(job_ids))
                )
            )
        delivered_keys = {(a, b, c) for a, b, c in delivery_rows}

    result = {
        "received_jobs": len(jobs),
        "matching_subscribers": 0,
        "email_attempted": 0,
        "email_delivered": 0,
        "telegram_attempted": 0,
        "telegram_delivered": 0,
        "failed": [],
    }

    for subscription in subscriptions:
        matching_jobs = [job for job in jobs if subscription_matches(subscription, job)]
        if not matching_jobs:
            continue
        result["matching_subscribers"] += 1

        if email_available:
            email_jobs = [
                job for job in matching_jobs
                if (subscription.id, job["id"], "email") not in delivered_keys
            ]
            if email_jobs:
                result["email_attempted"] += 1
                try:
                    send_job_email(app, subscription, email_jobs)
                    record_deliveries(engine, subscription.id, email_jobs, "email")
                    result["email_delivered"] += 1
                except Exception as exc:
                    app.logger.exception("Could not deliver email alert")
                    result["failed"].append(
                        f"email {mask_email(subscription.email)}: {exc}"
                    )

        connection = connections.get(subscription.id)
        if telegram_available and connection and connection.chat_id:
            telegram_jobs = [
                job for job in matching_jobs
                if (subscription.id, job["id"], "telegram") not in delivered_keys
            ]
            if telegram_jobs:
                result["telegram_attempted"] += 1
                try:
                    send_job_telegram(app, connection.chat_id, telegram_jobs)
                    record_deliveries(engine, subscription.id, telegram_jobs, "telegram")
                    result["telegram_delivered"] += 1
                except TelegramAPIError as exc:
                    app.logger.exception("Could not deliver Telegram alert")
                    if exc.error_code in {400, 403}:
                        disable_broken_telegram_connection(engine, connection.id)
                    result["failed"].append(
                        f"telegram {mask_email(subscription.email)}: {exc}"
                    )
                except Exception as exc:
                    app.logger.exception("Could not deliver Telegram alert")
                    result["failed"].append(
                        f"telegram {mask_email(subscription.email)}: {exc}"
                    )

    return result


def record_deliveries(
    engine, subscription_id: int, jobs: Iterable[dict[str, str]], channel: str
) -> None:
    with Session(engine) as session:
        for job in jobs:
            if job["id"]:
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


def send_job_email(
    app: Flask, subscription: Subscription, jobs: list[dict[str, str]]
) -> None:
    host = os.environ["SMTP_HOST"].strip()
    port = int(os.getenv("SMTP_PORT", "465"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.environ["ALERT_EMAIL_FROM"].strip()
    serializer = URLSafeSerializer(app.config["SECRET_KEY"], salt="unsubscribe")
    token = serializer.dumps(subscription.email)
    base_url = app.config.get("BASE_URL") or request.url_root.rstrip("/")
    unsubscribe_url = f"{base_url}/unsubscribe/{token}"

    message = EmailMessage()
    plural = "s" if len(jobs) != 1 else ""
    message["Subject"] = f"{len(jobs)} new U of M job match{plural}"
    message["From"] = sender
    message["To"] = subscription.email
    message.set_content(format_plain_email(jobs, unsubscribe_url))
    message.add_alternative(format_html_email(jobs, unsubscribe_url), subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)


def format_plain_email(jobs: Iterable[dict[str, str]], unsubscribe_url: str) -> str:
    blocks = ["New University of Manitoba job matches"]
    for job in jobs:
        blocks.append("\n".join((
            job["title"], f"Requisition: {job['id']}",
            f"Posted: {job['posting_date']}", job["url"],
        )))
    blocks.append(f"Unsubscribe: {unsubscribe_url}")
    return "\n\n".join(blocks)


def format_html_email(jobs: Iterable[dict[str, str]], unsubscribe_url: str) -> str:
    cards = []
    for job in jobs:
        cards.append(f'''<div style="border:1px solid #e5e7eb;border-radius:12px;padding:18px;margin:0 0 14px">
<div style="font-size:12px;color:#6b7280;margin-bottom:7px">Requisition {html.escape(job['id'])}</div>
<h2 style="font-size:18px;line-height:1.35;margin:0 0 8px;color:#1f2937">{html.escape(job['title'])}</h2>
<p style="margin:0 0 14px;color:#6b7280">Posted {html.escape(job['posting_date'])}</p>
<a href="{html.escape(job['url'])}" style="display:inline-block;background:#7b1734;color:#fff;text-decoration:none;border-radius:8px;padding:10px 14px;font-weight:700">View posting</a></div>''')
    return f'''<html><body style="margin:0;background:#f7f6f3;font-family:Arial,sans-serif;color:#1f2937"><div style="max-width:620px;margin:0 auto;padding:32px 18px"><div style="background:#fff;border-radius:16px;padding:26px"><div style="font-size:13px;font-weight:800;color:#7b1734">UM JOB ALERTS</div><h1>New jobs matched your preferences</h1>{''.join(cards)}<p style="font-size:12px;color:#6b7280">Independent alert service. Not affiliated with the University of Manitoba. <a href="{html.escape(unsubscribe_url)}">Unsubscribe</a>.</p></div></div></body></html>'''


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    digest = hashlib.sha256(local.encode("utf-8")).hexdigest()[:6]
    return f"user-{digest}@{domain}"

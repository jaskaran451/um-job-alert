from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable

from flask import Flask, jsonify, render_template, request
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


BASE_DIR = Path(__file__).resolve().parent
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ALLOWED_ROLE_TYPES = {
    "all",
    "teaching_assistant",
    "grader_marker",
    "instructor_sessionals",
    "technical_it",
    "research",
}
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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


class Base(DeclarativeBase):
    pass


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    role_types_json: Mapped[str] = mapped_column(Text, default="[]")
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    @property
    def role_types(self) -> list[str]:
        return safe_json_list(self.role_types_json)

    @property
    def keywords(self) -> list[str]:
        return safe_json_list(self.keywords_json)


def safe_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("APP_SECRET_KEY", "development-only-change-me"),
        DATABASE_URL=normalize_database_url(
            os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'subscribers.db'}")
        ),
        DISPATCH_API_KEY=os.getenv("DISPATCH_API_KEY", ""),
        BASE_URL=os.getenv("BASE_URL", "").rstrip("/"),
    )
    if test_config:
        app.config.update(test_config)

    database_url = app.config["DATABASE_URL"]
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    Base.metadata.create_all(engine)
    app.extensions["database_engine"] = engine

    @app.get("/")
    def index():
        latest_jobs, last_updated = load_latest_jobs()
        return render_template(
            "index.html", latest_jobs=latest_jobs[:6],
            tracked_job_count=len(latest_jobs), last_updated=last_updated,
        )

    @app.get("/healthz")
    def healthcheck():
        return jsonify(status="ok")

    @app.post("/api/subscriptions")
    def create_subscription():
        payload = request.get_json(silent=True) or {}
        if payload.get("company"):
            return jsonify(message="Subscription saved."), 201

        email = str(payload.get("email", "")).strip().casefold()
        role_types = sanitize_role_types(payload.get("role_types"))
        keywords = sanitize_keywords(payload.get("keywords"))
        consent = payload.get("consent") is True
        errors: dict[str, str] = {}
        if not EMAIL_PATTERN.fullmatch(email) or len(email) > 254:
            errors["email"] = "Enter a valid email address."
        if not role_types and not keywords:
            errors["preferences"] = "Choose at least one role type or keyword."
        if not consent:
            errors["consent"] = "Consent is required to send job alerts."
        if errors:
            return jsonify(message="Please review the highlighted fields.", errors=errors), 400

        with Session(engine) as session:
            subscription = session.scalar(select(Subscription).where(Subscription.email == email))
            if subscription is None:
                subscription = Subscription(email=email)
                session.add(subscription)
            subscription.role_types_json = json.dumps(role_types)
            subscription.keywords_json = json.dumps(keywords)
            subscription.active = True
            subscription.updated_at = utc_now()
            session.commit()

        return jsonify(message="Your alert preferences are saved.", email=email,
                       role_types=role_types, keywords=keywords), 201

    @app.post("/api/internal/dispatch")
    def dispatch_jobs():
        configured_key = app.config.get("DISPATCH_API_KEY", "")
        supplied_key = request.headers.get("X-Dispatch-Key", "")
        if not configured_key or not hmac.compare_digest(configured_key, supplied_key):
            return jsonify(message="Unauthorized."), 401

        payload = request.get_json(silent=True) or {}
        jobs = sanitize_jobs(payload.get("jobs"))
        if not jobs:
            return jsonify(message="No valid jobs supplied."), 400
        if not smtp_is_configured():
            return jsonify(message="Email delivery is not configured."), 503

        attempted = delivered = 0
        failed: list[str] = []
        with Session(engine) as session:
            subscriptions = list(session.scalars(select(Subscription).where(Subscription.active.is_(True))))

        for subscription in subscriptions:
            matching_jobs = [job for job in jobs if subscription_matches(subscription, job)]
            if not matching_jobs:
                continue
            attempted += 1
            try:
                send_job_email(app, subscription, matching_jobs)
                delivered += 1
            except Exception as exc:
                app.logger.exception("Could not deliver alert to %s", subscription.email)
                failed.append(f"{mask_email(subscription.email)}: {exc}")

        return jsonify(received_jobs=len(jobs), matching_subscribers=attempted,
                       delivered=delivered, failed=failed), 200 if not failed else 207

    @app.get("/unsubscribe/<token>")
    def unsubscribe(token: str):
        serializer = URLSafeSerializer(app.config["SECRET_KEY"], salt="unsubscribe")
        try:
            email = serializer.loads(token)
        except BadSignature:
            return render_template("unsubscribe.html", success=False), 400
        with Session(engine) as session:
            subscription = session.scalar(select(Subscription).where(Subscription.email == str(email).casefold()))
            if subscription:
                subscription.active = False
                subscription.updated_at = utc_now()
                session.commit()
        return render_template("unsubscribe.html", success=True)

    return app


def sanitize_role_types(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    unique: list[str] = []
    for item in value:
        role_type = str(item).strip()
        if role_type in ALLOWED_ROLE_TYPES and role_type not in unique:
            unique.append(role_type)
    return unique[:6]


def sanitize_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    unique: list[str] = []
    for item in value:
        keyword = " ".join(str(item).split()).strip(" ,")
        if 2 <= len(keyword) <= 40 and keyword.casefold() not in {x.casefold() for x in unique}:
            unique.append(keyword)
    return unique[:8]


def sanitize_jobs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    jobs: list[dict[str, str]] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title", "")).split())[:200]
        url = str(item.get("url") or item.get("detail_url") or "").strip()[:500]
        if not title or not url.startswith("https://viprecprod.ad.umanitoba.ca/"):
            continue
        jobs.append({"id": str(item.get("id") or item.get("requisition_id") or "")[:30],
                     "title": title, "posting_date": str(item.get("posting_date", ""))[:40], "url": url})
    return jobs


def load_latest_jobs() -> tuple[list[dict[str, str]], str | None]:
    try:
        state = json.loads((BASE_DIR / "data" / "seen_jobs.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    jobs = state.get("seen_jobs", [])
    return [job for job in jobs if isinstance(job, dict)], state.get("last_success_utc")


def subscription_matches(subscription: Subscription, job: dict[str, str]) -> bool:
    title = job["title"].casefold()
    if "all" in subscription.role_types:
        return True
    role_match = any(any(keyword in title for keyword in ROLE_KEYWORDS.get(role, ()))
                     for role in subscription.role_types)
    keyword_match = any(keyword.casefold() in title for keyword in subscription.keywords)
    return role_match or keyword_match


def smtp_is_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip() and os.getenv("ALERT_EMAIL_FROM", "").strip())


def send_job_email(app: Flask, subscription: Subscription, jobs: list[dict[str, str]]) -> None:
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
            smtp.ehlo(); smtp.starttls(context=context); smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)


def format_plain_email(jobs: Iterable[dict[str, str]], unsubscribe_url: str) -> str:
    blocks = ["New University of Manitoba job matches"]
    for job in jobs:
        blocks.append("\n".join((job["title"], f"Requisition: {job['id']}",
                                 f"Posted: {job['posting_date']}", job["url"])))
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


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)

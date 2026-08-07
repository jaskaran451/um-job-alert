from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request
from sqlalchemy import create_engine, desc, select, text
from sqlalchemy.orm import Session

from delivery_service import dispatch_to_subscribers
from models import (
    Base,
    MonitorState,
    PortalJob,
    Subscription,
    TelegramConnection,
    normalize_database_url,
    utc_now,
)
from telegram_service import (
    TelegramAPIError,
    issue_telegram_connect_link,
    process_telegram_update,
    telegram_linking_is_configured,
    telegram_sending_is_configured,
    validate_webhook_secret,
)


BASE_DIR = Path(__file__).resolve().parent
ALLOWED_ROLE_TYPES = {
    "all",
    "teaching_assistant",
    "grader_marker",
    "instructor_sessionals",
    "technical_it",
    "research",
}


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("APP_SECRET_KEY", "development-only-change-me"),
        MAX_CONTENT_LENGTH=16 * 1024,
        DATABASE_URL=normalize_database_url(
            os.getenv(
                "DATABASE_URL",
                f"sqlite:///{BASE_DIR / 'data' / 'subscribers.db'}",
            )
        ),
        DISPATCH_API_KEY=os.getenv("DISPATCH_API_KEY", ""),
        BASE_URL=os.getenv("BASE_URL", "").rstrip("/"),
        TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        TELEGRAM_BOT_USERNAME=os.getenv("TELEGRAM_BOT_USERNAME", "")
        .strip()
        .lstrip("@"),
        TELEGRAM_WEBHOOK_SECRET=os.getenv(
            "TELEGRAM_WEBHOOK_SECRET", ""
        ).strip(),
        TELEGRAM_CONNECT_TTL_MINUTES=int(
            os.getenv("TELEGRAM_CONNECT_TTL_MINUTES", "30")
        ),
        TELEGRAM_JOBS_PER_MESSAGE=int(
            os.getenv("TELEGRAM_JOBS_PER_MESSAGE", "8")
        ),
        TELEGRAM_MESSAGE_LIMIT=int(
            os.getenv("TELEGRAM_MESSAGE_LIMIT", "3800")
        ),
    )
    if test_config:
        app.config.update(test_config)

    database_url = app.config["DATABASE_URL"]
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    connect_args = (
        {"check_same_thread": False}
        if database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=connect_args,
    )
    Base.metadata.create_all(engine)
    app.extensions["database_engine"] = engine

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        return response

    @app.get("/")
    def index():
        latest_jobs, last_updated = load_latest_jobs(engine)
        return render_template(
            "index.html",
            latest_jobs=latest_jobs[:6],
            tracked_job_count=len(latest_jobs),
            last_updated=last_updated,
            telegram_available=telegram_linking_is_configured(app),
        )

    @app.get("/healthz")
    def healthcheck():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            app.logger.exception("Database health check failed")
            return jsonify(status="error", database="unavailable"), 503

        return jsonify(
            status="ok",
            database=engine.url.get_backend_name(),
            telegram=telegram_linking_is_configured(app),
        )

    @app.post("/api/subscriptions")
    def create_subscription():
        if not request.is_json:
            return jsonify(message="JSON request body required."), 415

        payload = request.get_json(silent=True) or {}
        if payload.get("company"):
            return jsonify(message="Preferences saved."), 201

        role_types = sanitize_role_types(payload.get("role_types"))
        keywords = sanitize_keywords(payload.get("keywords"))
        errors: dict[str, str] = {}

        if not role_types and not keywords:
            errors["preferences"] = (
                "Choose at least one role type or keyword."
            )
        if payload.get("consent") is not True:
            errors["consent"] = (
                "Consent is required to send Telegram job alerts."
            )
        if errors:
            return jsonify(
                message="Please review the highlighted fields.",
                errors=errors,
            ), 400

        if not telegram_linking_is_configured(app):
            return jsonify(
                message=(
                    "Telegram alerts are temporarily unavailable. "
                    "Please try again after the bot is configured."
                )
            ), 503

        purge_expired_unconnected_subscriptions(engine)

        internal_identifier = (
            f"telegram-{secrets.token_urlsafe(18).lower()}@alerts.invalid"
        )
        with Session(engine) as session:
            subscription = Subscription(
                email=internal_identifier,
                role_types_json=json.dumps(role_types),
                keywords_json=json.dumps(keywords),
                active=False,
                updated_at=utc_now(),
            )
            session.add(subscription)
            session.flush()
            subscription_id = subscription.id
            session.commit()

        connect_url, connected = issue_telegram_connect_link(
            app,
            engine,
            subscription_id,
        )

        return jsonify(
            message=(
                "Preferences saved. Connect Telegram to activate your alert."
            ),
            role_types=role_types,
            keywords=keywords,
            telegram_available=True,
            telegram_connected=connected,
            telegram_connect_url=connect_url,
            telegram_connect_expires_minutes=app.config[
                "TELEGRAM_CONNECT_TTL_MINUTES"
            ],
        ), 201

    @app.post("/api/internal/dispatch")
    def dispatch_jobs():
        configured_key = app.config.get("DISPATCH_API_KEY", "")
        supplied_key = request.headers.get("X-Dispatch-Key", "")
        if not configured_key or not hmac.compare_digest(
            configured_key,
            supplied_key,
        ):
            return jsonify(message="Unauthorized."), 401

        if not request.is_json:
            return jsonify(message="JSON request body required."), 415

        jobs = sanitize_jobs(
            (request.get_json(silent=True) or {}).get("jobs")
        )
        if not jobs:
            return jsonify(message="No valid jobs supplied."), 400

        if not telegram_sending_is_configured(app):
            return jsonify(
                message="Telegram delivery is not configured."
            ), 503

        result = dispatch_to_subscribers(app, engine, jobs)
        return jsonify(**result), 200 if not result["failed"] else 207

    @app.post("/api/telegram/webhook")
    def telegram_webhook():
        supplied_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )
        if not validate_webhook_secret(app, supplied_secret):
            return jsonify(ok=False), 401

        if not request.is_json:
            return jsonify(ok=False), 415

        try:
            process_telegram_update(
                app,
                engine,
                request.get_json(silent=True) or {},
            )
        except TelegramAPIError:
            app.logger.exception("Could not reply to Telegram update")
        except Exception:
            app.logger.exception("Could not process Telegram update")
            return jsonify(ok=False), 500

        return jsonify(ok=True)

    return app


def purge_expired_unconnected_subscriptions(engine) -> int:
    now = utc_now()
    removed = 0

    with Session(engine) as session:
        expired_connections = list(
            session.scalars(
                select(TelegramConnection).where(
                    TelegramConnection.enabled.is_(False),
                    TelegramConnection.chat_id.is_(None),
                    TelegramConnection.connect_expires_at.is_not(None),
                    TelegramConnection.connect_expires_at < now,
                )
            )
        )

        for connection in expired_connections:
            subscription = session.get(
                Subscription,
                connection.subscription_id,
            )
            session.delete(connection)
            if subscription is not None and not subscription.active:
                session.delete(subscription)
            removed += 1

        if removed:
            session.commit()

    return removed


def sanitize_role_types(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    result: list[str] = []
    for item in value:
        role = str(item).strip()
        if role in ALLOWED_ROLE_TYPES and role not in result:
            result.append(role)
    return result[:6]


def sanitize_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    result: list[str] = []
    for item in value:
        keyword = " ".join(str(item).split()).strip(" ,")
        if (
            2 <= len(keyword) <= 40
            and keyword.casefold()
            not in {existing.casefold() for existing in result}
        ):
            result.append(keyword)
    return result[:8]


def sanitize_jobs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    jobs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in value[:50]:
        if not isinstance(item, dict):
            continue

        title = " ".join(str(item.get("title", "")).split())[:200]
        url = str(
            item.get("url") or item.get("detail_url") or ""
        ).strip()[:500]
        job_id = str(
            item.get("id") or item.get("requisition_id") or ""
        )[:30]

        if not title or not url.startswith(
            "https://viprecprod.ad.umanitoba.ca/"
        ):
            continue

        key = (job_id, url)
        if key in seen:
            continue
        seen.add(key)

        jobs.append(
            {
                "id": job_id,
                "title": title,
                "posting_date": str(
                    item.get("posting_date", "")
                )[:40],
                "url": url,
            }
        )
    return jobs


def load_latest_jobs(engine) -> tuple[list[dict[str, str]], str | None]:
    """Load the live portal snapshot from PostgreSQL with a JSON fallback."""
    try:
        with Session(engine) as session:
            rows = list(
                session.scalars(
                    select(PortalJob)
                    .where(PortalJob.title != "")
                    .order_by(
                        desc(PortalJob.posted_on),
                        desc(PortalJob.job_id),
                    )
                    .limit(40)
                )
            )
            state = session.get(MonitorState, 1)

        if rows:
            return (
                [
                    {
                        "id": row.job_id,
                        "title": row.title,
                        "posting_date": row.posting_date,
                        "url": row.url,
                    }
                    for row in rows
                ],
                (
                    state.last_success_at.isoformat()
                    if state and state.last_success_at
                    else None
                ),
            )
    except Exception:
        # During a first deployment, retain the committed snapshot until the
        # cron service has completed its initial PostgreSQL migration.
        pass

    try:
        state = json.loads(
            (BASE_DIR / "data" / "seen_jobs.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return [], None

    jobs = state.get("seen_jobs", [])
    return (
        [job for job in jobs if isinstance(job, dict)],
        state.get("last_success_utc"),
    )


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "").casefold()
        in {"1", "true", "yes"},
    )

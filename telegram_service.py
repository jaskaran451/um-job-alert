from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from flask import Flask
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Subscription, TelegramConnection, utc_now


TELEGRAM_START_PATTERN = re.compile(
    r"^/start(?:@[A-Za-z0-9_]+)?(?:\s+([A-Za-z0-9_-]{10,64}))?\s*$",
    re.IGNORECASE,
)


class TelegramAPIError(RuntimeError):
    def __init__(
        self,
        description: str,
        error_code: int | None = None,
    ) -> None:
        super().__init__(description)
        self.description = description
        self.error_code = error_code


def normalized_expiry(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def telegram_linking_is_configured(app: Flask) -> bool:
    return bool(
        app.config.get("TELEGRAM_BOT_TOKEN")
        and app.config.get("TELEGRAM_BOT_USERNAME")
        and app.config.get("TELEGRAM_WEBHOOK_SECRET")
    )


def telegram_sending_is_configured(app: Flask) -> bool:
    return bool(app.config.get("TELEGRAM_BOT_TOKEN"))


def issue_telegram_connect_link(
    app: Flask,
    engine,
    subscription_id: int,
) -> tuple[str, bool]:
    raw_token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()
    expires_at = utc_now() + timedelta(
        minutes=app.config["TELEGRAM_CONNECT_TTL_MINUTES"]
    )

    with Session(engine) as session:
        connection = session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.subscription_id == subscription_id
            )
        )
        if connection is None:
            connection = TelegramConnection(
                subscription_id=subscription_id
            )
            session.add(connection)

        already_connected = bool(
            connection.enabled and connection.chat_id
        )
        connection.connect_token_hash = token_hash
        connection.connect_expires_at = expires_at
        connection.updated_at = utc_now()
        session.commit()

    username = app.config["TELEGRAM_BOT_USERNAME"]
    query = urllib.parse.urlencode({"start": raw_token})
    return f"https://t.me/{username}?{query}", already_connected


def validate_webhook_secret(
    app: Flask,
    supplied_secret: str,
) -> bool:
    configured = app.config.get("TELEGRAM_WEBHOOK_SECRET", "")
    return bool(configured) and hmac.compare_digest(
        configured,
        supplied_secret,
    )


def process_telegram_update(
    app: Flask,
    engine,
    update: dict[str, Any],
) -> None:
    message = update.get("message")
    if not isinstance(message, dict):
        return

    chat = message.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return

    chat_id = str(chat.get("id", "")).strip()
    if not chat_id:
        return

    text_value = message.get("text")
    text_message = (
        str(text_value).strip()
        if text_value is not None
        else ""
    )

    start_match = TELEGRAM_START_PATTERN.fullmatch(text_message)
    if start_match:
        token = start_match.group(1)
        if not token:
            send_telegram_message(
                app,
                chat_id,
                "Open the Create Telegram alert form on the UM Job "
                "Alerts website, save your preferences, then press "
                "Start from the private connection link.",
            )
            return

        connect_telegram_chat(
            app,
            engine,
            chat,
            token,
        )
        return

    command = (
        text_message.split(maxsplit=1)[0].casefold()
        if text_message
        else ""
    )
    command = command.split("@", 1)[0]

    if command == "/stop":
        stopped = disconnect_telegram_chat(engine, chat_id)
        reply = (
            "Telegram job alerts are disconnected. You will no "
            "longer receive job notifications in this chat."
            if stopped
            else "This Telegram chat is not currently connected "
            "to a job alert."
        )
        send_telegram_message(app, chat_id, reply)
        return

    if command == "/status":
        send_telegram_message(
            app,
            chat_id,
            telegram_connection_status(engine, chat_id),
        )
        return

    send_telegram_message(
        app,
        chat_id,
        "UM Job Alerts bot commands:\n"
        "/status — check whether alerts are active\n"
        "/stop — stop alerts in this chat\n\n"
        "To connect, save your preferences on the website and "
        "use its Connect Telegram button.",
    )


def connect_telegram_chat(
    app: Flask,
    engine,
    chat: dict[str, Any],
    raw_token: str,
) -> None:
    chat_id = str(chat.get("id", ""))
    token_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()
    now = utc_now()

    with Session(engine) as session:
        connection = session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.connect_token_hash == token_hash
            )
        )
        expiry = normalized_expiry(
            connection.connect_expires_at
            if connection
            else None
        )

        if connection is None or expiry is None or expiry < now:
            send_telegram_message(
                app,
                chat_id,
                "This connection link is invalid or expired. "
                "Return to the website, save your preferences "
                "again, and use the new Connect Telegram button.",
            )
            return

        existing = session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.chat_id == chat_id,
                TelegramConnection.id != connection.id,
            )
        )
        if existing:
            old_subscription = session.get(
                Subscription,
                existing.subscription_id,
            )
            if old_subscription:
                old_subscription.active = False
                old_subscription.updated_at = now

            existing.chat_id = None
            existing.username = None
            existing.first_name = None
            existing.enabled = False
            existing.updated_at = now

        subscription = session.get(
            Subscription,
            connection.subscription_id,
        )
        if subscription:
            subscription.active = True
            subscription.updated_at = now

        connection.chat_id = chat_id
        connection.username = normalize_optional_text(
            chat.get("username"),
            64,
        )
        connection.first_name = normalize_optional_text(
            chat.get("first_name"),
            128,
        )
        connection.enabled = True
        connection.connected_at = now
        connection.connect_token_hash = None
        connection.connect_expires_at = None
        connection.updated_at = now

        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            send_telegram_message(
                app,
                chat_id,
                "Telegram could not finish linking this chat. "
                "Generate a new link from the website and try again.",
            )
            return

    send_telegram_message(
        app,
        chat_id,
        "✅ Telegram alerts are connected. I’ll send only new "
        "University of Manitoba jobs matching the roles and "
        "keywords you saved.\n\n"
        "Use /status to check the connection or /stop to stop alerts.",
    )


def disconnect_telegram_chat(engine, chat_id: str) -> bool:
    with Session(engine) as session:
        connection = session.scalar(
            select(TelegramConnection).where(
                TelegramConnection.chat_id == chat_id,
                TelegramConnection.enabled.is_(True),
            )
        )
        if connection is None:
            return False

        subscription = session.get(
            Subscription,
            connection.subscription_id,
        )
        if subscription:
            subscription.active = False
            subscription.updated_at = utc_now()

        connection.chat_id = None
        connection.username = None
        connection.first_name = None
        connection.enabled = False
        connection.updated_at = utc_now()
        session.commit()
        return True


def telegram_connection_status(engine, chat_id: str) -> str:
    with Session(engine) as session:
        row = session.execute(
            select(TelegramConnection, Subscription)
            .join(
                Subscription,
                Subscription.id
                == TelegramConnection.subscription_id,
            )
            .where(
                TelegramConnection.chat_id == chat_id,
                TelegramConnection.enabled.is_(True),
                Subscription.active.is_(True),
            )
        ).first()

        if row is None:
            return (
                "This Telegram chat is not connected. Save your "
                "preferences on the website and use its Connect "
                "Telegram button."
            )

        return (
            "✅ Telegram job alerts are active in this chat. "
            "Use /stop whenever you want to stop them."
        )


def normalize_optional_text(
    value: Any,
    limit: int,
) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())[:limit]
    return normalized or None


def telegram_api_call(
    app: Flask,
    method: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    custom: Callable[
        [str, dict[str, Any]],
        dict[str, Any],
    ] | None = app.config.get("TELEGRAM_API_CALL")
    if custom:
        return custom(method, payload)

    token = app.config.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise TelegramAPIError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    endpoint = (
        f"https://api.telegram.org/bot{token}/{method}"
    )
    telegram_request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(
            telegram_request,
            timeout=30,
        ) as response:
            response_payload = json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as exc:
        try:
            response_payload = json.loads(
                exc.read().decode("utf-8")
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise TelegramAPIError(
                f"Telegram returned HTTP {exc.code}.",
                exc.code,
            ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TelegramAPIError(
            f"Telegram request failed: {exc}"
        ) from exc

    if not response_payload.get("ok"):
        raise TelegramAPIError(
            str(
                response_payload.get("description")
                or "Telegram API error."
            ),
            response_payload.get("error_code"),
        )

    return response_payload


def send_telegram_message(
    app: Flask,
    chat_id: str,
    message: str,
) -> None:
    telegram_api_call(
        app,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": True,
        },
    )


def telegram_chunks(
    message: str,
    limit: int = 3900,
) -> Iterable[str]:
    remaining = message
    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        yield remaining[:split_at]
        remaining = remaining[split_at:].lstrip()
    if remaining:
        yield remaining


def format_telegram_alert(
    jobs: Iterable[dict[str, str]],
) -> str:
    jobs_list = list(jobs)
    plural = "s" if len(jobs_list) != 1 else ""
    blocks = [
        f"🔔 {len(jobs_list)} new U of M job match{plural}"
    ]

    for job in jobs_list:
        details = [job["title"]]
        if job["id"]:
            details.append(f"Requisition: {job['id']}")
        if job["posting_date"]:
            details.append(f"Posted: {job['posting_date']}")
        details.append(job["url"])
        blocks.append("\n".join(details))

    return "\n\n".join(blocks)


def send_job_telegram(
    app: Flask,
    chat_id: str,
    jobs: list[dict[str, str]],
) -> None:
    for chunk in telegram_chunks(
        format_telegram_alert(jobs)
    ):
        send_telegram_message(app, chat_id, chunk)


def disable_broken_telegram_connection(
    engine,
    connection_id: int,
) -> None:
    with Session(engine) as session:
        connection = session.get(
            TelegramConnection,
            connection_id,
        )
        if connection:
            subscription = session.get(
                Subscription,
                connection.subscription_id,
            )
            if subscription:
                subscription.active = False
                subscription.updated_at = utc_now()

            connection.enabled = False
            connection.updated_at = utc_now()
            session.commit()

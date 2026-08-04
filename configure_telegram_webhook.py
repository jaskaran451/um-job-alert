#!/usr/bin/env python3
"""Configure the Telegram webhook used for per-user job alerts."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def telegram_call(token: str, method: str, payload: dict) -> dict:
    endpoint = f"https://api.telegram.org/bot{token}/{method}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram returned HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Telegram request failed: {exc}") from exc
    if not result.get("ok"):
        raise RuntimeError(result.get("description") or "Telegram API error")
    return result


def main() -> int:
    telegram_names = (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_BOT_USERNAME",
        "TELEGRAM_WEBHOOK_SECRET",
    )
    names = telegram_names + ("BASE_URL",)
    values = {name: os.getenv(name, "").strip() for name in names}
    if not any(values[name] for name in telegram_names):
        print(json.dumps({"telegram_webhook": "skipped", "reason": "not configured"}))
        return 0

    missing = [name for name, value in values.items() if not value]
    if missing:
        print(
            "Telegram is partially configured. Missing: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    base_url = values["BASE_URL"].rstrip("/")
    if not base_url.startswith("https://"):
        print("BASE_URL must be a public HTTPS URL.", file=sys.stderr)
        return 1

    token = values["TELEGRAM_BOT_TOKEN"]
    expected_username = values["TELEGRAM_BOT_USERNAME"].lstrip("@").casefold()
    bot = telegram_call(token, "getMe", {})["result"]
    actual_username = str(bot.get("username", "")).casefold()
    if not actual_username or actual_username != expected_username:
        print(
            "TELEGRAM_BOT_USERNAME does not match the bot returned by Telegram.",
            file=sys.stderr,
        )
        return 1

    webhook_url = f"{base_url}/api/telegram/webhook"
    telegram_call(
        token,
        "setWebhook",
        {
            "url": webhook_url,
            "secret_token": values["TELEGRAM_WEBHOOK_SECRET"],
            "allowed_updates": ["message"],
            "drop_pending_updates": False,
        },
    )
    telegram_call(
        token,
        "setMyCommands",
        {
            "commands": [
                {"command": "status", "description": "Check alert connection"},
                {"command": "stop", "description": "Disconnect Telegram alerts"},
            ]
        },
    )
    print(
        json.dumps(
            {
                "telegram_webhook": "configured",
                "bot_username": bot["username"],
                "url": webhook_url,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Dispatch queued jobs to Railway and clear the queue only on success."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.request

from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_state(path: Path) -> dict:
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise RuntimeError("State file must contain a JSON object.")
    pending = state.get("pending_jobs", [])
    if not isinstance(pending, list):
        raise RuntimeError("Invalid pending_jobs list.")
    return state


def save_state(path: Path, state: dict) -> None:
    serialized = json.dumps(state, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(serialized)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(os.getenv("STATE_FILE", "data/seen_jobs.json")),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    state = load_state(args.state_file)
    jobs = [
        item
        for item in state.get("pending_jobs", [])
        if isinstance(item, dict) and item.get("id")
    ]

    if not jobs:
        print("No pending jobs to dispatch.")
        return 0

    base = os.getenv("SUBSCRIBER_API_URL", "").strip().rstrip("/")
    key = os.getenv("SUBSCRIBER_API_KEY", "").strip()
    if not base or not key:
        print(
            f"Subscriber dispatch is not configured; keeping {len(jobs)} pending jobs."
        )
        return 0

    timeout_seconds = int(os.getenv("SUBSCRIBER_API_TIMEOUT_SECONDS", "120"))
    request = urllib.request.Request(
        base + "/api/internal/dispatch",
        data=json.dumps({"jobs": jobs}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Dispatch-Key": key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
        ) as response:
            raw_response = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"Dispatch failed HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(
            f"Dispatch did not complete; {len(jobs)} jobs remain pending: {exc}"
        ) from exc

    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Dispatch returned invalid JSON with HTTP {status}."
        ) from exc

    failures = payload.get("failed", [])
    if status >= 300 or failures:
        raise RuntimeError(
            f"Dispatch was incomplete; {len(jobs)} jobs remain pending: {payload}"
        )

    state["pending_jobs"] = []
    state["last_dispatch_utc"] = utc_now()
    save_state(args.state_file, state)
    print("Subscriber dispatch:", raw_response)
    print(f"Cleared {len(jobs)} successfully dispatched pending jobs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the production database connection and initialize the schema."""

from __future__ import annotations

import json
import os
import sys

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session


def main() -> int:
    if not os.getenv("DATABASE_URL", "").strip():
        print(
            "DATABASE_URL is missing. Add a Railway reference variable from "
            "the PostgreSQL service to the web service.",
            file=sys.stderr,
        )
        return 1

    # Importing app initializes SQLAlchemy and creates the MVP schema when it
    # does not exist yet.
    from app import Subscription, app

    engine = app.extensions["database_engine"]

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    tables = inspect(engine).get_table_names()
    if Subscription.__tablename__ not in tables:
        print("The subscriptions table was not created.", file=sys.stderr)
        return 1

    with Session(engine) as session:
        subscriber_count = session.scalar(
            select(func.count()).select_from(Subscription)
        )

    print(
        json.dumps(
            {
                "status": "ok",
                "database": engine.url.get_backend_name(),
                "subscriptions_table": True,
                "subscriber_count": int(subscriber_count or 0),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

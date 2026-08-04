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

    from app import Delivery, Subscription, TelegramConnection, app

    engine = app.extensions["database_engine"]

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    tables = set(inspect(engine).get_table_names())
    required_tables = {
        Subscription.__tablename__,
        TelegramConnection.__tablename__,
        Delivery.__tablename__,
    }
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        print(
            "Required tables were not created: " + ", ".join(missing_tables),
            file=sys.stderr,
        )
        return 1

    with Session(engine) as session:
        subscriber_count = session.scalar(
            select(func.count()).select_from(Subscription)
        )
        telegram_count = session.scalar(
            select(func.count())
            .select_from(TelegramConnection)
            .where(TelegramConnection.enabled.is_(True))
        )

    print(
        json.dumps(
            {
                "status": "ok",
                "database": engine.url.get_backend_name(),
                "tables": sorted(required_tables),
                "subscriber_count": int(subscriber_count or 0),
                "telegram_connections": int(telegram_count or 0),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

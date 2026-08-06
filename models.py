from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def safe_json_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


class Base(DeclarativeBase):
    pass


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    role_types_json: Mapped[str] = mapped_column(Text, default="[]")
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    @property
    def role_types(self) -> list[str]:
        return safe_json_list(self.role_types_json)

    @property
    def keywords(self) -> list[str]:
        return safe_json_list(self.keywords_json)


class TelegramConnection(Base):
    __tablename__ = "telegram_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    chat_id: Mapped[str | None] = mapped_column(
        String(32),
        unique=True,
        index=True,
    )
    # Retained only for schema compatibility. Public alerts do not need or
    # store Telegram profile names.
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    connect_token_hash: Mapped[str | None] = mapped_column(
        String(64),
        unique=True,
        index=True,
    )
    connect_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )

    @validates("username", "first_name")
    def discard_profile_metadata(self, key: str, value: str | None) -> None:
        del key, value
        return None


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "job_id",
            "channel",
            name="uq_delivery_job_channel",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        index=True,
    )
    job_id: Mapped[str] = mapped_column(String(30), index=True)
    channel: Mapped[str] = mapped_column(String(20), index=True)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
    )

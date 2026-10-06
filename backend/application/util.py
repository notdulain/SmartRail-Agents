"""Small shared helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def month_key(moment: datetime | None = None) -> str:
    return (moment or utcnow()).strftime("%Y-%m")


def iso(moment: datetime) -> str:
    return moment.isoformat()


def parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

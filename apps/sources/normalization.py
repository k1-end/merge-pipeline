"""Helpers for normalizing provider timestamps and states."""

from datetime import UTC, datetime

from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware


def provider_datetime(value: str | None) -> datetime:
    """Parse an ISO timestamp and return a timezone-aware value."""
    parsed = parse_datetime(value or "")
    if parsed is None:
        raise ValueError(f"Invalid provider timestamp: {value!r}")
    return make_aware(parsed, UTC) if parsed.tzinfo is None else parsed

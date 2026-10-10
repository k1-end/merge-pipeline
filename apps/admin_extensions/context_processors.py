"""Shared display settings for the operations dashboard."""

from django.http import HttpRequest
from django.utils import timezone


def server_timezone(request: HttpRequest) -> dict[str, str]:
    """Keep every dashboard timestamp in the configured server timezone."""
    return {"server_timezone": timezone.get_default_timezone_name()}

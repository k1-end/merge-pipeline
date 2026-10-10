"""Regression coverage for server-local dashboard timestamps."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings
from django.utils import timezone


@pytest.mark.parametrize(
    ("server_zone", "timestamp", "expected"),
    [
        ("Asia/Tehran", datetime(2026, 1, 1, 22, 0, tzinfo=UTC), "Jan 2, 01:30"),
        ("Europe/Berlin", datetime(2026, 1, 1, 22, 0, tzinfo=UTC), "Jan 1, 23:00"),
        ("Europe/Berlin", datetime(2026, 7, 1, 22, 0, tzinfo=UTC), "Jul 2, 00:00"),
        ("UTC", datetime(2026, 1, 1, 22, 0, tzinfo=UTC), "Jan 1, 22:00"),
    ],
)
def test_dashboard_dates_and_live_clock_use_server_timezone(server_zone, timestamp, expected):
    """Browser/user timezone must not override the server's display timezone."""
    context = {
        "recent_merge_requests": [
            SimpleNamespace(
                pk=1,
                opened_at=timestamp,
                title="Timezone regression",
            )
        ],
        "recent_reviews": [],
    }
    with override_settings(TIME_ZONE=server_zone), timezone.override("America/Los_Angeles"):
        html = render_to_string(
            "admin_extensions/dashboard.html", context, request=RequestFactory().get("/")
        )

    assert expected in html
    assert f'"{server_zone}"' in html
    assert "timeZone: serverTimezone" in html
    assert "toLocaleTimeString()" not in html


@pytest.mark.parametrize(
    ("environment", "localtime", "timezone_file", "expected"),
    [
        ("Europe/Berlin", "/usr/share/zoneinfo/Asia/Tehran", "UTC", "Europe/Berlin"),
        (":Asia/Tehran", "/etc/localtime", "UTC", "Asia/Tehran"),
        (None, "/usr/share/zoneinfo/Asia/Tehran", "UTC", "Asia/Tehran"),
        (None, "/etc/localtime", "Europe/Berlin\n", "Europe/Berlin"),
        (None, "/etc/localtime", "Invalid/Zone", "UTC"),
    ],
)
def test_system_timezone_detection(monkeypatch, environment, localtime, timezone_file, expected):
    """Resolve an IANA timezone rather than a fixed offset or browser timezone."""
    from pathlib import Path

    from config.timezones import get_server_timezone

    if environment is None:
        monkeypatch.delenv("TZ", raising=False)
    else:
        monkeypatch.setenv("TZ", environment)
    monkeypatch.setattr(Path, "resolve", lambda self: Path(localtime))
    monkeypatch.setattr(Path, "read_text", lambda self: timezone_file)

    assert get_server_timezone() == expected


def test_system_timezone_missing_files(monkeypatch):
    """Minimal containers without timezone configuration default safely to UTC."""
    from pathlib import Path

    from config.timezones import get_server_timezone

    def missing(self):
        raise OSError("Timezone file unavailable")

    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(Path, "resolve", missing)
    monkeypatch.setattr(Path, "read_text", missing)

    assert get_server_timezone() == "UTC"

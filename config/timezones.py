"""Resolve the server's system timezone for Django's default display zone."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_server_timezone() -> str:
    """Return the system IANA timezone, falling back to UTC in minimal containers."""
    candidates = [os.environ.get("TZ", "").removeprefix(":")]
    try:
        localtime = str(Path("/etc/localtime").resolve())
        if "/zoneinfo/" in localtime:
            candidates.append(localtime.split("/zoneinfo/", 1)[1])
    except OSError:
        pass
    try:
        candidates.append(Path("/etc/timezone").read_text().strip())
    except OSError:
        pass
    for name in candidates:
        try:
            ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            continue
        return name
    return "UTC"

"""Resolve findings to actual old/new lines in an immutable unified diff."""

import re
from typing import Any

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class NoCommentableLineError(ValueError):
    """Publication is blocked because the request has no text diff lines."""


def diff_locations(changes: list[dict]) -> list[dict[str, Any]]:
    """Enumerate eligible lines, rejecting incomplete or malformed hunks."""
    locations = []
    for change in changes:
        old = new = old_left = new_left = 0
        hunk_locations: list[dict[str, Any]] = []
        for text in (change.get("diff") or "").splitlines():
            match = HUNK.match(text)
            if match:
                if old_left == new_left == 0:
                    locations.extend(hunk_locations)
                hunk_locations = []
                old, old_left, new, new_left = (
                    int(match[1]),
                    int(match[2] or 1),
                    int(match[3]),
                    int(match[4] or 1),
                )
                continue
            if not (old_left or new_left) or not text or text[0] not in " +-":
                continue
            prefix = text[0]
            uses_old, uses_new = prefix != "+", prefix != "-"
            if (uses_old and old_left <= 0) or (uses_new and new_left <= 0):
                hunk_locations = []
                old_left = new_left = 0
                continue
            hunk_locations.append(
                {
                    "old_path": change.get("old_path") or change["file_path"],
                    "new_path": change["file_path"],
                    "old_line": old if uses_old else None,
                    "new_line": new if uses_new else None,
                }
            )
            old += int(uses_old)
            new += int(uses_new)
            old_left -= int(uses_old)
            new_left -= int(uses_new)
        if old_left == new_left == 0:
            locations.extend(hunk_locations)
    return locations


def resolve_location(
    changes: list[dict], file_path: str, line: int | None, side: str = "new"
) -> dict[str, Any]:
    """Prefer the supplied location, then the same file, then another text file."""
    side = "old" if side == "old" else "new"
    locations = diff_locations(changes)
    same_file = [p for p in locations if file_path in {p["old_path"], p["new_path"]}]
    if type(line) is int and line > 0:
        for position in same_file:
            if position[f"{side}_line"] == line:
                return {**position, "side": side, "fallback": False}
    candidates = same_file or locations
    if not candidates:
        raise NoCommentableLineError(
            "No commentable text lines; inline publication requires human resolution"
        )
    # Prefer the new side; deleted-only files necessarily use the old side.
    anchor = next((p for p in candidates if p["new_line"] is not None), candidates[0])
    return {**anchor, "side": "new" if anchor["new_line"] is not None else "old", "fallback": True}

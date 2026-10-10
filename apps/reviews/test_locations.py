"""Inline placement guarantees, including file-level fallbacks."""

import pytest

from apps.reviews.locations import NoCommentableLineError, resolve_location


def changes():
    """Return a diff with both old and new coordinates."""
    return [{"file_path": "app.py", "old_path": "app.py", "diff": "@@ -10,2 +10,2 @@\n context\n-old\n+new"}]


def test_exact_new_line():
    """A valid line keeps its precise location."""
    location = resolve_location(changes(), "app.py", 11)
    assert (location["new_line"], location["fallback"]) == (11, False)


@pytest.mark.parametrize("line", [None, 1, -1, True, 500])
def test_missing_or_invalid_line_uses_first_commentable_line(line):
    """Fallback never assumes line one exists in a diff."""
    location = resolve_location(changes(), "app.py", line)
    assert location["new_line"] == 10
    assert location["fallback"] is True


def test_cross_file_fallback():
    """Binary or unknown files use an explicitly labelled cross-file anchor."""
    location = resolve_location(changes(), "logo.png", None)
    assert location["new_path"] == "app.py"
    assert location["fallback"] is True


def test_deleted_file_uses_old_side():
    """Deleted-only hunks remain commentable on the old side."""
    location = resolve_location([{"file_path": "gone.py", "diff": "@@ -1 +0,0 @@\n-gone"}], "gone.py", None)
    assert location["old_line"] == 1
    assert location["new_line"] is None


def test_no_text_lines_blocks_publication():
    """Binary-only requests cannot silently become general comments."""
    with pytest.raises(NoCommentableLineError):
        resolve_location([{"file_path": "logo.png", "diff": ""}], "logo.png", None)


def test_renamed_paths_are_preserved():
    """Provider positions contain both rename paths."""
    location = resolve_location([{"file_path": "new.py", "old_path": "old.py", "diff": "@@ -1 +1 @@\n same"}], "old.py", 1)
    assert (location["old_path"], location["new_path"]) == ("old.py", "new.py")

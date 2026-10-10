"""Provider-specific inline API contracts."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.sources.github.adaptor import GitHubAdaptor
from apps.sources.gitlab.adaptor import GitLabAdaptor


@pytest.mark.parametrize("kind", [GitLabAdaptor, GitHubAdaptor])
def test_inline_comment_payload(kind):
    """Inline creation never uses the general notes/issue endpoint."""
    adaptor = object.__new__(kind)
    adaptor.client = Mock()
    adaptor.client.request_json.return_value = {"id": "thread", "notes": [{"id": 42}]} if kind is GitLabAdaptor else {"id": 42}
    position = {"old_path": "old.py", "new_path": "new.py", "old_line": None, "new_line": 4}
    revision = {"base_sha": "base", "start_sha": "start", "head_sha": "head"}
    result = adaptor.create_comment(SimpleNamespace(external_id="1", full_name="org/repo"), "2", position, revision, "finding")
    assert result == "42"
    method, endpoint, payload = adaptor.client.request_json.call_args.args
    assert method == "POST"
    if kind is GitLabAdaptor:
        assert endpoint == "projects/1/merge_requests/2/discussions"
        assert payload["position"]["new_line"] == 4
        assert payload["position"]["head_sha"] == "head"
    else:
        assert endpoint == "repos/org/repo/pulls/2/comments"
        assert payload["side"] == "RIGHT"
        assert payload["commit_id"] == "head"


@pytest.mark.parametrize("kind", [GitLabAdaptor, GitHubAdaptor])
def test_inline_marker_recovery(kind):
    """A retry discovers existing inline comments, not only general comments."""
    adaptor = object.__new__(kind)
    adaptor.client = Mock()
    marker = "<!-- marker -->"
    if kind is GitLabAdaptor:
        adaptor.client.get_json.side_effect = [[{"id": "thread", "notes": [{"id": 42, "body": marker}]}]]
    else:
        adaptor.client.get_json.side_effect = [[{"id": 42, "body": marker}]]
    assert adaptor.find_comment_by_marker(SimpleNamespace(external_id="1", full_name="org/repo"), "2", marker) == "42"
    assert "/discussions" in adaptor.client.get_json.call_args.args[0] if kind is GitLabAdaptor else "/pulls/2/comments" in adaptor.client.get_json.call_args.args[0]


def test_github_old_context_coordinate():
    """An old-side finding never accidentally moves to the new-side number."""
    adaptor = object.__new__(GitHubAdaptor)
    adaptor.client = Mock()
    adaptor.client.request_json.return_value = {"id": 42}
    adaptor.create_comment(SimpleNamespace(full_name="org/repo"), "2", {"new_path": "app.py", "old_line": 10, "new_line": 20, "side": "old"}, {"head_sha": "head"}, "finding")
    payload = adaptor.client.request_json.call_args.args[2]
    assert (payload["side"], payload["line"]) == ("LEFT", 10)


def test_github_snapshot_detects_revision_race():
    """A revision change during paginated diff reads is rejected."""
    from apps.sources.exceptions import AdaptorConfigurationError

    adaptor = object.__new__(GitHubAdaptor)
    adaptor.client = Mock()
    adaptor.client.get_json.side_effect = [{"head": {"sha": "a"}, "base": {"sha": "base"}}, [], {"head": {"sha": "b"}, "base": {"sha": "base"}}]
    with pytest.raises(AdaptorConfigurationError, match="changed"):
        adaptor.fetch_review_snapshot(SimpleNamespace(full_name="org/repo"), "2")


def test_gitlab_snapshot_requires_complete_revision():
    """Incomplete GitLab diffs cannot be used for inline review publication."""
    from apps.sources.exceptions import AdaptorConfigurationError

    adaptor = object.__new__(GitLabAdaptor)
    adaptor.client = Mock()
    adaptor.client.get_json.return_value = {"changes": [], "diff_refs": {"head_sha": "head"}}
    with pytest.raises(AdaptorConfigurationError, match="incomplete"):
        adaptor.fetch_review_snapshot(SimpleNamespace(external_id="1"), "2")

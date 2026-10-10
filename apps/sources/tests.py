from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import requests
from cryptography.fernet import Fernet

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.sources.client import SourceClient
from apps.sources.exceptions import AdaptorConfigurationError, AdaptorConnectionError, AdaptorError
from apps.sources.factory import get_adaptor
from apps.sources.github.adaptor import GitHubAdaptor
from apps.sources.gitlab.adaptor import GitLabAdaptor
from apps.sources.models import GitSource


@pytest.fixture
def source(db: Any) -> GitSource:
    return GitSource.objects.create(
        name="company-gitlab", source_type=GitSource.GITLAB, base_url="https://git.example/api/v4"
    )


@pytest.fixture
def credential(settings: Any, source: GitSource) -> GitSourceCredential:
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    return GitSourceCredential.objects.create(
        source=source,
        access_token_encrypted=CredentialEncryption().encrypt("test-token"),
    )


def test_source_string_and_defaults(source: GitSource) -> None:
    assert str(source) == "company-gitlab"
    assert source.is_active is True
    assert source.sync_enabled is True


def test_factory_builds_registered_adaptor(source: GitSource, credential: GitSourceCredential) -> None:
    assert get_adaptor(source).__class__.__name__ == "GitLabAdaptor"


def test_factory_requires_credentials(source: GitSource) -> None:
    with pytest.raises(AdaptorError, match="no credentials"):
        get_adaptor(source)


def test_factory_rejects_unknown_source_type(source: GitSource, credential: GitSourceCredential) -> None:
    source.source_type = "unknown"
    source.save(update_fields=["source_type"])
    with pytest.raises(AdaptorError, match="No adaptor registered"):
        get_adaptor(source)


def test_gitlab_fetch_merge_requests_paginates(source: GitSource, credential: GitSourceCredential) -> None:
    adaptor = GitLabAdaptor(source, credential)
    item = {
        "id": 1,
        "iid": 1,
        "title": "MR",
        "state": "opened",
        "author": {"username": "user", "id": 2},
        "source_branch": "feature",
        "target_branch": "main",
        "web_url": "https://git.example/mr/1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    adaptor.client = Mock()
    adaptor.client.get_json.side_effect = [[item] * 100, [item]]

    result = adaptor.fetch_merge_requests(SimpleNamespace(external_id="10"))

    assert len(result) == 101
    assert adaptor.client.get_json.call_args_list[1].kwargs["params"]["page"] == 2


def test_github_fetch_merge_requests_paginates(source: GitSource, credential: GitSourceCredential) -> None:
    source.source_type = GitSource.GITHUB
    source.save(update_fields=["source_type"])
    adaptor = GitHubAdaptor(source, credential)
    item = {
        "id": 1,
        "number": 1,
        "title": "PR",
        "user": {"login": "user", "id": 2},
        "head": {"ref": "feature"},
        "base": {"ref": "main"},
        "html_url": "https://github.com/example/repo/pull/1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    adaptor.client = Mock()
    adaptor.client.get_json.side_effect = [[item] * 100, [item]]

    result = adaptor.fetch_merge_requests(SimpleNamespace(full_name="example/repo"))

    assert len(result) == 101
    assert adaptor.client.get_json.call_args_list[1].kwargs["params"]["page"] == 2


def test_github_fetch_merge_requests_rejects_noncanonical_repository() -> None:
    """GitHub API paths require a namespace-qualified repository name."""
    adaptor = GitHubAdaptor.__new__(GitHubAdaptor)
    adaptor.client = Mock()

    with pytest.raises(AdaptorConfigurationError, match="owner/repository"):
        adaptor.fetch_merge_requests(SimpleNamespace(full_name="k1.nvim"))

    adaptor.client.get_json.assert_not_called()


def test_gitlab_find_comment_by_marker(source: GitSource, credential: GitSourceCredential) -> None:
    """GitLab comment lookup scans discussions for the idempotency marker."""
    adaptor = GitLabAdaptor(source, credential)
    adaptor.client = Mock()
    adaptor.client.get_json.return_value = [{"id": "thread", "notes": [{"id": 42, "body": "message\n<!-- marker -->"}]}]

    result = adaptor.find_comment_by_marker(SimpleNamespace(external_id="10"), "7", "<!-- marker -->")

    assert result == "42"
    adaptor.client.get_json.assert_called_once_with(
        "projects/10/merge_requests/7/discussions",
        params={"per_page": 100, "page": 1},
    )


def test_github_find_comment_by_marker(source: GitSource, credential: GitSourceCredential) -> None:
    """GitHub comment lookup scans inline-comment pages for the idempotency marker."""
    source.source_type = GitSource.GITHUB
    source.save(update_fields=["source_type"])
    adaptor = GitHubAdaptor(source, credential)
    adaptor.client = Mock()
    adaptor.client.get_json.return_value = [{"id": 42, "body": "message\n<!-- marker -->"}]

    result = adaptor.find_comment_by_marker(SimpleNamespace(full_name="org/repo"), "7", "<!-- marker -->")

    assert result == "42"
    adaptor.client.get_json.assert_called_once_with(
        "repos/org/repo/pulls/7/comments",
        params={"per_page": 100, "page": 1},
    )


def test_github_fetch_mr_discussion_normalizes_comments_and_reviews(
    source: GitSource, credential: GitSourceCredential
) -> None:
    source.source_type = GitSource.GITHUB
    source.save(update_fields=["source_type"])
    adaptor = GitHubAdaptor(source, credential)
    adaptor.client = Mock()
    adaptor.client.get_json.side_effect = [
        [
            {
                "id": 10,
                "user": {"login": "alice", "id": 1},
                "body": "General feedback",
                "html_url": "https://github.com/org/repo/pull/7#issuecomment-10",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ],
        [
            {
                "id": 11,
                "pull_request_review_id": 20,
                "in_reply_to_id": 9,
                "user": {"login": "bob", "id": 2},
                "body": "Inline feedback",
                "path": "app.py",
                "line": 12,
                "side": "RIGHT",
                "commit_id": "abc",
                "html_url": "https://github.com/org/repo/pull/7#discussion_r11",
                "created_at": "2026-01-02T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
            }
        ],
        [
            {
                "id": 20,
                "user": {"login": "bob", "id": 2},
                "body": "Please revise",
                "state": "CHANGES_REQUESTED",
                "commit_id": "abc",
                "html_url": "https://github.com/org/repo/pull/7#pullrequestreview-20",
                "submitted_at": "2026-01-02T00:00:00Z",
            }
        ],
    ]

    comments = adaptor.fetch_mr_comments(SimpleNamespace(full_name="org/repo"), "7")
    reviews = adaptor.fetch_mr_reviews(SimpleNamespace(full_name="org/repo"), "7")

    assert [comment["provider_kind"] for comment in comments] == [
        "github_issue_comment",
        "github_review_comment",
    ]
    assert comments[1]["file_path"] == "app.py"
    assert comments[1]["parent_external_id"] == "9"
    assert reviews[0]["state"] == "changes_requested"


def test_gitlab_fetch_mr_comments_preserves_threads_and_excludes_system_notes(
    source: GitSource, credential: GitSourceCredential
) -> None:
    adaptor = GitLabAdaptor(source, credential)
    adaptor.client = Mock()
    adaptor.client.get_json.return_value = [
        {
            "id": "thread-1",
            "notes": [
                {
                    "id": 10,
                    "system": True,
                    "body": "changed title",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "id": 11,
                    "system": False,
                    "author": {"username": "alice", "id": 1},
                    "body": "Please revise",
                    "resolvable": True,
                    "resolved": False,
                    "position": {"new_path": "app.py", "new_line": 8, "head_sha": "abc"},
                    "created_at": "2026-01-02T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                },
            ],
        }
    ]

    comments = adaptor.fetch_mr_comments(SimpleNamespace(external_id="10"), "7")

    assert len(comments) == 1
    assert comments[0]["external_thread_id"] == "thread-1"
    assert comments[0]["category"] == "inline"
    assert comments[0]["is_resolvable"] is True


def test_github_fetch_merge_requests_normalizes_closed_and_merged_states() -> None:
    adaptor = GitHubAdaptor.__new__(GitHubAdaptor)
    item = {
        "id": 1,
        "number": 147,
        "title": "Closed PR",
        "user": {"login": "user", "id": 2},
        "head": {"ref": "feature"},
        "base": {"ref": "main"},
        "html_url": "https://github.com/example/repo/pull/147",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "closed_at": "2026-01-02T00:00:00Z",
        "merged_at": None,
    }

    normalized = adaptor._normalize_merge_request(item)

    assert normalized["state"] == "closed"
    assert normalized["closed_at"] is not None


def test_source_client_does_not_treat_http_404_as_transient(
    settings: Any,
) -> None:
    """Provider 4xx responses are configuration failures, not retryable outages."""
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    response = requests.Response()
    response.status_code = 404
    response.url = "https://api.github.com/repos/missing/repo/pulls"
    client = SourceClient(
        "https://api.github.com",
        CredentialEncryption().encrypt("token"),
    )
    client.session.get = Mock(side_effect=requests.HTTPError(response=response))

    with pytest.raises(AdaptorConfigurationError, match="HTTP 404"):
        client.get("repos/missing/repo/pulls")


def test_source_client_treats_rate_limit_as_transient(settings: Any) -> None:
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    response = requests.Response()
    response.status_code = 429
    response.url = "https://api.github.com/repos/org/repo/issues/7/comments"
    client = SourceClient(
        "https://api.github.com",
        CredentialEncryption().encrypt("token"),
    )
    client.session.get = Mock(side_effect=requests.HTTPError(response=response))

    with pytest.raises(AdaptorConnectionError, match="rate limit"):
        client.get("repos/org/repo/issues/7/comments")

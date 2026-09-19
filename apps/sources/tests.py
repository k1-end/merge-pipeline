from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import requests
from cryptography.fernet import Fernet

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.sources.client import SourceClient
from apps.sources.exceptions import AdaptorConfigurationError, AdaptorError
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


def test_factory_rejects_unknown_source_type(
    source: GitSource, credential: GitSourceCredential
) -> None:
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
    client.session.get = Mock(
        side_effect=requests.HTTPError(response=response)
    )

    with pytest.raises(AdaptorConfigurationError, match="HTTP 404"):
        client.get("repos/missing/repo/pulls")

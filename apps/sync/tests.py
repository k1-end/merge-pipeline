import hashlib
import hmac
import json
from typing import Any
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.projects.models import MergeRequest, Project
from apps.sources.exceptions import AdaptorConfigurationError
from apps.sources.models import GitSource
from apps.sync.models import WebhookEvent
from apps.sync.services import SyncService
from apps.sync.tasks import sync_project


@pytest.fixture
def source(settings: Any, db: Any) -> GitSource:
    """Create a signed GitHub source."""
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    source = GitSource.objects.create(
        name="test-github", source_type=GitSource.GITHUB, base_url="https://api.github.com", webhook_secret="hook-secret"
    )
    GitSourceCredential.objects.create(source=source, access_token_encrypted=CredentialEncryption().encrypt("token"))
    return source


def test_github_webhook_requires_valid_signature(client: Any, source: GitSource, monkeypatch: Any) -> None:
    """Invalid webhook signatures are rejected before persistence."""
    payload = {"action": "opened"}
    body = json.dumps(payload).encode()
    monkeypatch.setattr("apps.sources.views.get_adaptor", lambda source: Mock(verify_webhook=lambda body, signature: False))

    response = client.post(f"/webhooks/github/{source.id}/", data=body, content_type="application/json")

    assert response.status_code == 401
    assert not WebhookEvent.objects.exists()


def test_github_webhook_persists_and_queues_signed_event(client: Any, source: GitSource, monkeypatch: Any) -> None:
    """A valid webhook is stored and queued for asynchronous processing."""
    payload = {"action": "opened"}
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    task = Mock()
    monkeypatch.setattr("apps.sources.views.process_webhook_event", task)

    response = client.post(
        f"/webhooks/github/{source.id}/", data=body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature, HTTP_X_GITHUB_EVENT="pull_request",
    )

    assert response.status_code == 202
    assert WebhookEvent.objects.get().event_type == "pull_request"
    task.delay.assert_called_once()


def test_sync_project_does_not_retry_configuration_errors(monkeypatch: Any) -> None:
    """Invalid provider metadata should fail without consuming retry attempts."""
    project = Mock()
    project.objects = Mock()
    monkeypatch.setattr("apps.projects.models.Project.objects", project.objects)
    project.objects.select_related.return_value.get.return_value = object()

    monkeypatch.setattr(
        "apps.sync.services.SyncService.sync_project",
        Mock(side_effect=AdaptorConfigurationError("invalid repository")),
    )

    with pytest.raises(AdaptorConfigurationError, match="invalid repository"):
        sync_project.apply(args=(1,), throw=True)


def test_sync_source_does_not_clear_existing_clone_url(source: GitSource, monkeypatch: Any) -> None:
    """A provider response without clone metadata must not invalidate an existing cache target."""
    project = Project.objects.create(
        source=source,
        external_id="1",
        name="api",
        full_name="org/api",
        web_url="https://github.com/org/api",
        clone_url="https://github.com/org/api.git",
    )
    adaptor = Mock(
        fetch_projects=Mock(
            return_value=[
                {
                    "external_id": "1",
                    "name": "api",
                    "full_name": "org/api",
                    "web_url": "https://github.com/org/api",
                    "clone_url": "",
                }
            ]
        ),
        sync_project=Mock(return_value={"new_mrs": 0, "updated_mrs": 0}),
    )
    monkeypatch.setattr("apps.sync.services.get_adaptor", lambda source: adaptor)

    SyncService().sync_source(source)

    project.refresh_from_db()
    assert project.clone_url == "https://github.com/org/api.git"


def test_reconcile_active_merge_requests_closes_stale_local_request(
    source: GitSource, monkeypatch: Any
) -> None:
    """A locally open request is updated when the provider reports it closed."""
    project = Project.objects.create(
        source=source,
        external_id="1",
        name="api",
        full_name="org/api",
        web_url="https://github.com/org/api",
    )
    mr = MergeRequest.objects.create(
        project=project,
        external_id="external-147",
        external_iid="147",
        title="Closed PR",
        state=MergeRequest.OPENED,
        author_username="user",
        source_branch="feature",
        target_branch="main",
        web_url="https://github.com/org/api/pull/147",
        opened_at="2026-01-01T00:00:00Z",
        external_updated_at="2026-01-01T00:00:00Z",
    )
    adaptor = Mock(
        fetch_open_merge_request_iids=Mock(return_value=set()),
        fetch_merge_request=Mock(
            return_value={
                "state": MergeRequest.CLOSED,
                "external_updated_at": "2026-01-02T00:00:00Z",
                "merged_at": None,
                "closed_at": "2026-01-02T00:00:00Z",
            }
        ),
    )
    monkeypatch.setattr("apps.sync.services.get_adaptor", lambda source: adaptor)

    result = SyncService().reconcile_active_merge_requests(project)

    mr.refresh_from_db()
    assert result == {"checked": 1, "updated": 1, "unchanged": 0, "failed": 0}
    assert mr.state == MergeRequest.CLOSED

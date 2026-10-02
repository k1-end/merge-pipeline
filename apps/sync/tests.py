import hashlib
import hmac
import json
from datetime import datetime, timezone as dt_timezone
from typing import Any
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from django.utils import timezone

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.projects.models import MergeRequest, Project
from apps.reviews.models import AIProvider, Review
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


def test_github_webhook_deduplicates_delivery_and_does_not_store_signature(
    client: Any, source: GitSource, monkeypatch: Any
) -> None:
    """Repeated provider deliveries are accepted without duplicate processing."""
    payload = {"action": "created"}
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    task = Mock()
    monkeypatch.setattr("apps.sources.views.process_webhook_event", task)
    request_args = {
        "data": body,
        "content_type": "application/json",
        "HTTP_X_HUB_SIGNATURE_256": signature,
        "HTTP_X_GITHUB_EVENT": "issue_comment",
        "HTTP_X_GITHUB_DELIVERY": "delivery-1",
    }

    first = client.post(f"/webhooks/github/{source.id}/", **request_args)
    second = client.post(f"/webhooks/github/{source.id}/", **request_args)

    event = WebhookEvent.objects.get()
    assert first.status_code == second.status_code == 202
    assert event.delivery_id == "delivery-1"
    assert "X-Hub-Signature-256" not in event.headers
    task.delay.assert_called_once_with(event.id)


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


def test_sync_project_reconciles_stale_active_merge_requests(
    source: GitSource, monkeypatch: Any
) -> None:
    """A regular sync marks a request merged when it was merged remotely."""
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
        fetch_merge_requests=Mock(return_value=[]),
        fetch_open_merge_request_iids=Mock(return_value=set()),
        fetch_merge_request=Mock(
            return_value={
                "state": MergeRequest.MERGED,
                "external_updated_at": "2026-01-02T00:00:00Z",
                "merged_at": "2026-01-02T00:00:00Z",
                "closed_at": None,
            }
        ),
    )
    monkeypatch.setattr("apps.sync.services.get_adaptor", lambda source: adaptor)

    SyncService().sync_project(project)

    mr.refresh_from_db()
    assert mr.state == MergeRequest.MERGED


def test_sync_project_does_not_skip_merge_request_created_during_previous_sync(
    source: GitSource, monkeypatch: Any
) -> None:
    """The next incremental sync imports requests created while the previous sync was running."""
    previous_cursor = datetime(2026, 9, 28, 6, 45, tzinfo=dt_timezone.utc)
    sync_started_at = datetime(2026, 9, 28, 6, 51, 36, tzinfo=dt_timezone.utc)
    merge_request_updated_at = datetime(2026, 9, 28, 6, 51, 42, tzinfo=dt_timezone.utc)
    sync_finished_at = datetime(2026, 9, 28, 6, 52, 58, tzinfo=dt_timezone.utc)
    project = Project.objects.create(
        source=source,
        external_id="1",
        name="api",
        full_name="org/api",
        web_url="https://github.com/org/api",
        last_synced_at=previous_cursor,
    )
    item = {
        "external_id": "external-2661",
        "external_iid": "2661",
        "title": "Created during sync",
        "description": "",
        "state": MergeRequest.OPENED,
        "author_username": "user",
        "author_external_id": "1",
        "source_branch": "feature",
        "source_sha": "a" * 40,
        "target_branch": "main",
        "web_url": "https://github.com/org/api/pull/2661",
        "opened_at": merge_request_updated_at,
        "external_updated_at": merge_request_updated_at,
        "merged_at": None,
        "closed_at": None,
    }
    clock = {"now": sync_started_at}
    fetch_count = 0

    def fetch_merge_requests(_project: Project, updated_after: datetime) -> list[dict[str, Any]]:
        nonlocal fetch_count
        fetch_count += 1
        if fetch_count == 1:
            clock["now"] = sync_finished_at
            return []
        return [item] if updated_after < merge_request_updated_at else []

    adaptor = Mock(
        fetch_merge_requests=Mock(side_effect=fetch_merge_requests),
        fetch_mr_changes=Mock(return_value=[]),
    )
    monkeypatch.setattr("apps.sync.services.get_adaptor", lambda _source: adaptor)
    monkeypatch.setattr("apps.sync.services.timezone.now", lambda: clock["now"])
    monkeypatch.setattr(SyncService, "reconcile_active_merge_requests", Mock())
    monkeypatch.setattr(SyncService, "sync_merge_request_discussion", Mock())

    SyncService().sync_project(project)
    SyncService().sync_project(project)

    assert MergeRequest.objects.filter(project=project, external_iid="2661").exists()
    assert adaptor.fetch_merge_requests.call_args_list[1].args[1] == sync_started_at


def test_sync_project_queues_auto_review_after_commit_for_new_revision(
    source: GitSource,
    monkeypatch: Any,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """New merge requests and later head commits each reserve one automatic review."""
    provider = AIProvider.objects.create(
        name="auto", provider_type=AIProvider.CUSTOM, model_name="mock", is_default=True
    )
    project = Project.objects.create(
        source=source,
        external_id="1",
        name="api",
        full_name="org/api",
        web_url="https://github.com/org/api",
        clone_url="https://github.com/org/api.git",
        auto_review_enabled=True,
        ai_provider=provider,
    )
    item = {
        "external_id": "external-12",
        "external_iid": "12",
        "title": "Review me",
        "description": "Change",
        "state": MergeRequest.OPENED,
        "author_username": "user",
        "author_external_id": "1",
        "source_branch": "feature",
        "source_sha": "a" * 40,
        "target_branch": "main",
        "web_url": "https://github.com/org/api/pull/12",
        "opened_at": timezone.now(),
        "external_updated_at": timezone.now(),
        "merged_at": None,
        "closed_at": None,
    }
    adaptor = Mock(fetch_merge_requests=Mock(return_value=[item]), fetch_mr_changes=Mock(return_value=[]))
    monkeypatch.setattr("apps.sync.services.get_adaptor", lambda _source: adaptor)
    monkeypatch.setattr(SyncService, "reconcile_active_merge_requests", Mock())
    monkeypatch.setattr(SyncService, "sync_merge_request_discussion", Mock())
    apply_async = Mock()
    monkeypatch.setattr("apps.reviews.tasks.execute_ai_review.apply_async", apply_async)

    with django_capture_on_commit_callbacks(execute=True):
        SyncService().sync_project(project)

    first_review = Review.objects.get()
    assert first_review.status == Review.PENDING
    apply_async.assert_called_once_with(
        args=(first_review.pk,), task_id=first_review.celery_task_id
    )

    item["source_sha"] = "b" * 40
    item["external_updated_at"] = timezone.now()
    with django_capture_on_commit_callbacks(execute=True):
        SyncService().sync_project(project)

    assert Review.objects.count() == 2
    assert apply_async.call_count == 2

    with django_capture_on_commit_callbacks(execute=True):
        SyncService().sync_project(project)

    assert Review.objects.count() == 2
    assert apply_async.call_count == 2

from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.projects.models import MergeRequest, Project
from apps.sizing.models import MergeRequestSizeResult, SizeLabelPolicy
from apps.sizing.services import SizeLabelService
from apps.sizing.tasks import label_merge_request_size
from apps.sources.github.adaptor import GitHubAdaptor
from apps.sources.gitlab.adaptor import GitLabAdaptor
from apps.sources.models import GitSource


@pytest.fixture
def merge_request(db: Any) -> MergeRequest:
    source = GitSource.objects.create(
        name="git", source_type=GitSource.GITLAB, base_url="https://git.example/api/v4"
    )
    project = Project.objects.create(
        source=source,
        external_id="42",
        name="service",
        full_name="platform/service",
        web_url="https://git.example/platform/service",
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return MergeRequest.objects.create(
        project=project,
        external_id="100",
        external_iid="7",
        title="Change",
        description="",
        state=MergeRequest.OPENED,
        author_username="dev",
        source_branch="feature",
        source_sha="abc123",
        target_branch="main",
        web_url="https://git.example/platform/service/-/merge_requests/7",
        opened_at=now,
        external_updated_at=now,
    )


@pytest.mark.django_db
def test_default_policy_classifies_boundaries() -> None:
    policy = SizeLabelPolicy.load()
    assert policy.classify(10) == "size/XS"
    assert policy.classify(11) == "size/S"
    assert policy.classify(250) == "size/M"
    assert policy.classify(1001) == "size/XL"


@pytest.mark.django_db
def test_policy_rejects_unordered_thresholds() -> None:
    policy = SizeLabelPolicy(xs_max=10, s_max=10, m_max=250, l_max=1000)
    with pytest.raises(ValidationError):
        policy.full_clean()


def test_size_task_uses_dedicated_queue() -> None:
    assert label_merge_request_size.queue == "size-labeling"


def test_github_size_sums_native_file_stats() -> None:
    adaptor = GitHubAdaptor.__new__(GitHubAdaptor)
    adaptor._fetch_pages = Mock(
        return_value=[{"additions": 4, "deletions": 2}, {"additions": 10, "deletions": 3}]
    )
    project = Mock(full_name="org/repo")
    assert adaptor.fetch_mr_size(project, "7") == (14, 5)


def test_gitlab_size_ignores_diff_headers() -> None:
    adaptor = GitLabAdaptor.__new__(GitLabAdaptor)
    adaptor.client = Mock()
    adaptor.client.get_json.return_value = {
        "overflow": False,
        "changes": [{"diff": "--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n-old\n+new\n+extra"}],
    }
    project = Mock(external_id="42")
    assert adaptor.fetch_mr_size(project, "7") == (2, 1)


def test_gitlab_replaces_only_managed_labels() -> None:
    adaptor = GitLabAdaptor.__new__(GitLabAdaptor)
    adaptor.client = Mock()
    adaptor.client.get_json.return_value = {"labels": ["backend", "size/S"]}
    project = Mock(external_id="42")
    adaptor.replace_size_label(project, "7", "size/M")
    adaptor.client.request_json.assert_called_once_with(
        "PUT",
        "projects/42/merge_requests/7",
        {"add_labels": "size/M", "remove_labels": "size/S"},
    )


def test_service_persists_and_posts_size(merge_request: MergeRequest, monkeypatch: Any) -> None:
    adaptor = Mock()
    adaptor.fetch_mr_size.return_value = (30, 25)
    monkeypatch.setattr("apps.sizing.services.get_adaptor", lambda source: adaptor)
    result = SizeLabelService().label(merge_request.pk, "abc123")
    assert result is not None
    assert result.changed_lines == 55
    assert result.label == "size/M"
    assert result.status == MergeRequestSizeResult.COMPLETED
    adaptor.replace_size_label.assert_called_once_with(merge_request.project, "7", "size/M")


def test_staff_can_update_size_thresholds(client: Any, db: Any, monkeypatch: Any) -> None:
    user = get_user_model().objects.create_user(username="staff", password="pw", is_staff=True)
    client.force_login(user)
    delay = Mock()
    monkeypatch.setattr("apps.sizing.tasks.queue_pending_size_labels.delay", delay)
    response = client.post(
        reverse("admin_extensions:size_labels"),
        {"enabled": "on", "xs_max": 20, "s_max": 75, "m_max": 300, "l_max": 1200},
    )
    assert response.status_code == 302
    policy = SizeLabelPolicy.load()
    assert (policy.xs_max, policy.s_max, policy.m_max, policy.l_max) == (20, 75, 300, 1200)
    assert policy.revision == 2
    delay.assert_called_once_with()

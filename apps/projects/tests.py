from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from django.db import IntegrityError

from apps.projects.models import MergeRequest, MRChange, Project
from apps.sources.models import GitSource
from apps.sync.services import SyncService


@pytest.fixture
def source(db: Any) -> GitSource:
    """Create a source for project synchronization tests."""
    return GitSource.objects.create(
        name="test-gitlab", source_type=GitSource.GITLAB, base_url="https://git.example/api/v4"
    )


@pytest.fixture
def project(source: GitSource) -> Project:
    """Create a repository with a stable external identifier."""
    return Project.objects.create(
        source=source,
        external_id="42",
        name="service",
        full_name="platform/service",
        web_url="https://git.example/platform/service",
    )


def test_project_external_id_is_unique_per_source(source: GitSource) -> None:
    """A repository cannot be imported twice from one source."""
    Project.objects.create(
        source=source,
        external_id="42",
        name="one",
        full_name="platform/one",
        web_url="https://git.example/platform/one",
    )
    with pytest.raises(IntegrityError):
        Project.objects.create(
            source=source,
            external_id="42",
            name="two",
            full_name="platform/two",
            web_url="https://git.example/platform/two",
        )


def test_sync_project_is_idempotent(project: Project, monkeypatch: Any) -> None:
    """Repeated provider snapshots update one MR and replace its file snapshot."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    adaptor = Mock()
    adaptor.fetch_merge_requests.return_value = [{
        "external_id": "mr-1", "external_iid": "7", "title": "Fix", "description": "",
        "state": "opened", "author_username": "dev", "author_external_id": "1",
        "source_branch": "fix", "target_branch": "main", "web_url": "https://git.example/mr/7",
        "opened_at": now, "external_updated_at": now, "merged_at": None, "closed_at": None,
    }]
    adaptor.fetch_mr_changes.return_value = [{"file_path": "app.py", "old_path": "", "change_type": "modified", "diff": "@@"}]
    monkeypatch.setattr("apps.sync.services.get_adaptor", lambda source: adaptor)

    first = SyncService().sync_project(project)
    second = SyncService().sync_project(project)

    assert first["new_mrs"] == 1
    assert second["updated_mrs"] == 1
    assert MergeRequest.objects.filter(project=project).count() == 1
    assert MRChange.objects.filter(merge_request__project=project).count() == 1

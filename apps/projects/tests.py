from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from django.db import IntegrityError

from apps.projects.models import (
    MergeRequest,
    MRChange,
    MRDiscussionSyncState,
    Project,
    RemoteMRComment,
    RemoteMRReview,
)
from apps.reviews.models import AIProvider, Review, ReviewComment
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
    adaptor.fetch_merge_requests.return_value = [
        {
            "external_id": "mr-1",
            "external_iid": "7",
            "title": "Fix",
            "description": "",
            "state": "opened",
            "author_username": "dev",
            "author_external_id": "1",
            "source_branch": "fix",
            "target_branch": "main",
            "web_url": "https://git.example/mr/7",
            "opened_at": now,
            "external_updated_at": now,
            "merged_at": None,
            "closed_at": None,
        }
    ]
    adaptor.fetch_mr_changes.return_value = [
        {"file_path": "app.py", "old_path": "", "change_type": "modified", "diff": "@@"}
    ]
    adaptor.fetch_mr_comments.return_value = []
    adaptor.fetch_mr_reviews.return_value = []
    adaptor.fetch_open_merge_request_iids.return_value = {"7"}
    monkeypatch.setattr("apps.sync.services.get_adaptor", lambda source: adaptor)

    first = SyncService().sync_project(project)
    second = SyncService().sync_project(project)

    assert first["new_mrs"] == 1
    assert second["updated_mrs"] == 1
    assert MergeRequest.objects.filter(project=project).count() == 1
    assert MRChange.objects.filter(merge_request__project=project).count() == 1


def test_sync_merge_request_discussion_upserts_links_and_tombstones(project: Project) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    mr = MergeRequest.objects.create(
        project=project,
        external_id="mr-1",
        external_iid="7",
        title="Fix",
        state=MergeRequest.OPENED,
        author_username="dev",
        source_branch="fix",
        target_branch="main",
        web_url="https://git.example/mr/7",
        opened_at=now,
        external_updated_at=now,
    )
    provider = AIProvider.objects.create(name="test", provider_type=AIProvider.CUSTOM, model_name="test")
    review = Review.objects.create(merge_request=mr, provider=provider, status=Review.COMPLETED)
    local_comment = ReviewComment.objects.create(
        review=review,
        file_path="app.py",
        comment_type="issue",
        content="AI finding",
        external_comment_id="10",
    )
    comment = {
        "provider_kind": RemoteMRComment.GITLAB_NOTE,
        "category": RemoteMRComment.GENERAL,
        "external_id": "10",
        "external_thread_id": "thread-1",
        "external_review_id": "",
        "parent_external_id": "",
        "author_username": "bot",
        "author_external_id": "1",
        "body": "AI finding",
        "web_url": "",
        "file_path": "",
        "line_number": None,
        "start_line_number": None,
        "line_side": "",
        "commit_sha": "",
        "is_resolvable": False,
        "is_resolved": False,
        "provider_created_at": now,
        "provider_updated_at": now,
    }
    remote_review = {
        "external_id": "20",
        "author_username": "maintainer",
        "author_external_id": "2",
        "body": "Approved",
        "state": "approved",
        "commit_sha": "abc",
        "web_url": "",
        "provider_created_at": now,
        "provider_updated_at": now,
    }
    adaptor = Mock()
    adaptor.fetch_mr_comments.return_value = [comment]
    adaptor.fetch_mr_reviews.return_value = [remote_review]

    SyncService().sync_merge_request_discussion(mr, adaptor=adaptor)

    synced = RemoteMRComment.objects.get(merge_request=mr, external_id="10")
    assert synced.local_review_comment == local_comment
    assert RemoteMRReview.objects.filter(merge_request=mr, external_id="20").exists()
    assert MRDiscussionSyncState.objects.get(merge_request=mr).last_full_sync_at is not None

    adaptor.fetch_mr_comments.return_value = []
    adaptor.fetch_mr_reviews.return_value = []
    SyncService().sync_merge_request_discussion(mr, adaptor=adaptor)

    synced.refresh_from_db()
    assert synced.deleted_at is not None
    assert RemoteMRReview.objects.get(merge_request=mr, external_id="20").deleted_at is not None

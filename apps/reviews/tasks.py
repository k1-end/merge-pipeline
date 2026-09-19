from typing import Any

from celery import shared_task

from apps.reviews.repository_cache import RepositoryCacheMissingError


def _raise_or_retry_missing_cache(task: Any, project: Any, exc: RepositoryCacheMissingError) -> None:
    """Retry only while an explicitly requested cache clone is still running."""
    from apps.projects.models import Project

    project.refresh_from_db(fields=["cache_status"])
    if project.cache_status == Project.CACHE_CLONING:
        raise task.retry(exc=exc, countdown=30) from exc
    raise exc


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def clone_repository_cache(self: Any, project_id: int) -> int:
    """Clone one repository into persistent Docker-managed cache storage."""
    from django.utils import timezone

    from apps.projects.models import Project
    from apps.reviews.repository_cache import (
        RepositoryCache,
        RepositoryCacheConfigurationError,
    )

    project = Project.objects.select_related("source").get(id=project_id)
    project.cache_status = Project.CACHE_CLONING
    project.cache_error = ""
    project.save(update_fields=["cache_status", "cache_error", "updated_at"])
    try:
        RepositoryCache(project).clone()
    except RepositoryCacheConfigurationError as exc:
        project.cache_status = Project.CACHE_ERROR
        project.cache_error = str(exc)
        project.cache_updated_at = timezone.now()
        project.save(update_fields=["cache_status", "cache_error", "cache_updated_at", "updated_at"])
        raise
    except Exception as exc:  # noqa: BLE001 - Celery owns transient retry policy
        if self.request.retries >= self.max_retries:
            project.cache_status = Project.CACHE_ERROR
            project.cache_updated_at = timezone.now()
            project.save(update_fields=["cache_status", "cache_updated_at", "updated_at"])
            raise
        project.cache_error = str(exc)
        project.save(update_fields=["cache_error", "updated_at"])
        raise self.retry(exc=exc) from exc
    project.cache_status = Project.CACHE_READY
    project.cache_error = ""
    project.cache_updated_at = timezone.now()
    project.save(update_fields=["cache_status", "cache_error", "cache_updated_at", "updated_at"])
    return project.id


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def run_ai_review(self: Any, mr_id: int) -> int:
    """Run a review for an eligible merge request."""
    from apps.projects.models import MergeRequest
    from apps.reviews.pipeline import ReviewPipeline

    mr = MergeRequest.objects.select_related("project", "project__ai_provider").get(id=mr_id)
    if not mr.project.auto_review_enabled or (mr.state == "draft" and not mr.project.review_on_draft):
        return 0
    try:
        return ReviewPipeline().run_review(mr).id
    except RepositoryCacheMissingError as exc:
        _raise_or_retry_missing_cache(self, mr.project, exc)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def run_manual_ai_review(self: Any, mr_id: int) -> int:
    """Run a manually requested review without automatic-review eligibility gates."""
    from apps.projects.models import MergeRequest
    from apps.reviews.pipeline import ReviewPipeline

    mr = MergeRequest.objects.select_related("project", "project__ai_provider").get(id=mr_id)
    try:
        return ReviewPipeline().run_review(mr).id
    except RepositoryCacheMissingError as exc:
        _raise_or_retry_missing_cache(self, mr.project, exc)
    except Exception as exc:  # noqa: BLE001 - Celery owns retry policy
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def post_review_comments(self: Any, review_id: int) -> int:
    """Publish pending comments for a completed review."""
    from apps.reviews.models import Review
    from apps.reviews.services import ReviewActionService

    review = Review.objects.get(id=review_id)
    try:
        return ReviewActionService.post_comments(review)
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc) from exc

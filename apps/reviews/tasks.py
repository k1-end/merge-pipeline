from typing import Any

from celery import shared_task

from apps.reviews.repository_cache import RepositoryCacheMissingError


def _reset_review_for_retry(review: Any) -> None:
    """Return a failed attempt to pending before Celery retries the same reservation."""
    if review is not None:
        type(review).objects.filter(pk=review.pk, status=review.FAILED).update(
            status=review.PENDING,
            error_message="",
            completed_at=None,
        )


def _has_retries_remaining(task: Any) -> bool:
    """Return whether Celery can retry, tolerating lightweight task doubles in tests."""
    try:
        return bool(task.request.retries < task.max_retries)
    except TypeError:
        return True


def _prepare_review_execution(review: Any, task_id: str | None) -> bool:
    """Make a redelivered task executable after its previous worker disappeared."""
    if review.status == review.PENDING:
        return True
    if review.status != review.PROCESSING or not task_id or review.celery_task_id != task_id:
        return False
    recovered = type(review).objects.filter(
        pk=review.pk,
        status=review.PROCESSING,
        celery_task_id=task_id,
    ).update(
        status=review.PENDING,
        error_message="",
        completed_at=None,
    )
    if recovered:
        review.status = review.PENDING
    return bool(recovered)


def _raise_or_retry_missing_cache(
    task: Any, project: Any, exc: RepositoryCacheMissingError, review: Any = None
) -> None:
    """Retry only while an explicitly requested cache clone is still running."""
    from apps.projects.models import Project

    project.refresh_from_db(fields=["cache_status"])
    if project.cache_status == Project.CACHE_CLONING:
        if _has_retries_remaining(task):
            _reset_review_for_retry(review)
        raise task.retry(exc=exc, countdown=30) from exc
    raise exc


def _retry_review(task: Any, review: Any, exc: Exception) -> None:
    """Retry a failed review while preserving one durable review record."""
    if _has_retries_remaining(task):
        _reset_review_for_retry(review)
    else:
        from django.utils import timezone

        now = timezone.now()
        type(review).objects.filter(
            pk=review.pk, status__in=(review.PENDING, review.FAILED)
        ).update(
            status=review.FAILED,
            error_message=str(exc),
            completed_at=now,
            updated_at=now,
        )
    raise task.retry(exc=exc) from exc


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
def execute_ai_review(self: Any, review_id: int) -> int:
    """Run a review for an eligible merge request."""
    from django.utils import timezone

    from apps.projects.models import MergeRequest
    from apps.reviews.models import Review
    from apps.reviews.pipeline import ReviewPipeline, ReviewStateError

    review = Review.objects.select_related(
        "provider", "merge_request__project", "merge_request__project__source"
    ).get(id=review_id)
    if not _prepare_review_execution(review, self.request.id):
        return 0
    mr: MergeRequest = review.merge_request
    eligible = (
        mr.project.is_active
        and mr.project.source.is_active
        and mr.project.auto_review_enabled
        and review.provider.is_active
        and mr.state in {MergeRequest.OPENED, MergeRequest.DRAFT}
        and (mr.state != MergeRequest.DRAFT or mr.project.review_on_draft)
    )
    if not eligible:
        now = timezone.now()
        Review.objects.filter(pk=review.pk, status=Review.PENDING).update(
            status=Review.CANCELLED,
            error_message="Automatic review is no longer eligible.",
            completed_at=now,
            updated_at=now,
        )
        return 0
    try:
        return ReviewPipeline().run_review(mr, pending_review=review).id
    except ReviewStateError:
        return 0
    except RepositoryCacheMissingError as exc:
        _raise_or_retry_missing_cache(self, mr.project, exc, review)
    except Exception as exc:  # noqa: BLE001
        _retry_review(self, review, exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def execute_manual_ai_review(self: Any, review_id: int) -> int:
    """Run a manually requested review without automatic-review eligibility gates."""
    from apps.reviews.models import Review
    from apps.reviews.pipeline import ReviewPipeline, ReviewStateError

    review = Review.objects.select_related(
        "provider", "merge_request__project", "merge_request__project__source"
    ).get(id=review_id)
    if not _prepare_review_execution(review, self.request.id):
        return 0
    if not review.provider.is_active:
        from django.utils import timezone

        now = timezone.now()
        Review.objects.filter(pk=review.pk, status=Review.PENDING).update(
            status=Review.CANCELLED,
            error_message="The selected AI provider is no longer active.",
            completed_at=now,
            updated_at=now,
        )
        return 0
    mr = review.merge_request
    try:
        return ReviewPipeline().run_review(mr, pending_review=review).id
    except ReviewStateError:
        return 0
    except RepositoryCacheMissingError as exc:
        _raise_or_retry_missing_cache(self, mr.project, exc, review)
    except Exception as exc:  # noqa: BLE001 - Celery owns retry policy
        _retry_review(self, review, exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def run_ai_review(self: Any, mr_id: int) -> int:
    """Compatibility wrapper for automatic jobs queued with a merge-request ID."""
    from apps.projects.models import MergeRequest
    from apps.reviews.services import ReviewAlreadyRunningError, ReviewQueueService

    mr = MergeRequest.objects.select_related("project", "project__source", "project__ai_provider").get(
        id=mr_id
    )
    try:
        return int(ReviewQueueService.queue_automatic_review(mr) is not None)
    except ReviewAlreadyRunningError:
        return 0


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def run_manual_ai_review(self: Any, mr_id: int) -> int:
    """Compatibility wrapper for manual jobs queued with a merge-request ID."""
    from apps.projects.models import MergeRequest
    from apps.reviews.services import ReviewAlreadyRunningError, ReviewQueueService

    mr = MergeRequest.objects.select_related("project", "project__source", "project__ai_provider").get(
        id=mr_id
    )
    try:
        ReviewQueueService.queue_manual_review(mr)
    except ReviewAlreadyRunningError:
        return 0
    return 1


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

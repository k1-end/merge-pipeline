"""Review action services."""

import logging
from typing import Any
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from apps.reviews.locations import NoCommentableLineError, resolve_location
from apps.reviews.models import AIProvider, Review
from apps.reviews.repository_cache import RepositoryCache, RepositoryCacheMissingError
from apps.sources.exceptions import AdaptorConfigurationError, AdaptorConnectionError
from apps.sources.factory import get_adaptor

logger = logging.getLogger(__name__)


class ReviewAlreadyRunningError(RuntimeError):
    """Raised when a merge request already has an active review."""


class ReviewConfigurationError(ValueError):
    """Raised when a merge request cannot be reviewed with current settings."""


class ReviewQueueService:
    """Queue manually requested reviews from the operations dashboard."""

    @staticmethod
    def queue_manual_review(merge_request: Any) -> str:
        """Queue one review unless the merge request already has an active one."""
        from apps.reviews.tasks import execute_manual_ai_review

        return ReviewQueueService._queue_review(merge_request, execute_manual_ai_review)

    @staticmethod
    def queue_automatic_review(merge_request: Any) -> str | None:
        """Queue an eligible automatic review, or return ``None`` when it is ineligible."""
        project = merge_request.project
        if not project.auto_review_enabled:
            return None
        if merge_request.state not in {merge_request.OPENED, merge_request.DRAFT}:
            return None
        if merge_request.state == merge_request.DRAFT and not project.review_on_draft:
            return None
        from apps.reviews.tasks import execute_ai_review

        return ReviewQueueService._queue_review(merge_request, execute_ai_review, allow_new_revision=True)

    @staticmethod
    def _queue_review(merge_request: Any, task: Any, *, allow_new_revision: bool = False) -> str:
        """Reserve a pending review transactionally, then publish its Celery task."""
        from apps.projects.models import MergeRequest

        with transaction.atomic():
            merge_request = (
                MergeRequest.objects.select_for_update()
                .select_related("project", "project__source", "project__ai_provider")
                .get(pk=merge_request.pk)
            )
            provider = ReviewQueueService._validate_configuration(merge_request)
            active_reviews = Review.objects.filter(
                merge_request=merge_request,
                status__in=(Review.PENDING, Review.PROCESSING),
            )
            duplicate_exists = (
                active_reviews.filter(head_sha=merge_request.source_sha).exists()
                if allow_new_revision
                else active_reviews.exists()
            )
            if duplicate_exists:
                raise ReviewAlreadyRunningError("A review is already running for this merge request.")

            task_id = uuid4().hex
            review = Review.objects.create(
                merge_request=merge_request,
                provider=provider,
                status=Review.PENDING,
                celery_task_id=task_id,
                source_branch=merge_request.source_branch,
                target_branch=merge_request.target_branch,
                head_sha=merge_request.source_sha,
            )

            def publish() -> None:
                try:
                    task.apply_async(args=(review.pk,), task_id=task_id)
                except Exception as exc:
                    Review.objects.filter(pk=review.pk, status=Review.PENDING).update(
                        status=Review.FAILED,
                        error_message=f"Could not publish review task: {exc}",
                        completed_at=timezone.now(),
                        updated_at=timezone.now(),
                    )
                    logger.exception("Could not publish review task", extra={"review_id": review.pk})
                    raise

            transaction.on_commit(publish)
        return task_id

    @staticmethod
    def _validate_configuration(merge_request: Any) -> AIProvider:
        """Return the selected provider after validating review prerequisites."""
        project = merge_request.project
        if not project.is_active or not project.source.is_active:
            raise ReviewConfigurationError("The project or Git source is inactive.")
        if not project.clone_url:
            raise ReviewConfigurationError("The project has no clone URL.")
        provider = project.ai_provider or AIProvider.objects.filter(is_default=True, is_active=True).first()
        if provider is None or not provider.is_active:
            raise ReviewConfigurationError("No active AI provider is configured.")
        if provider.provider_type == AIProvider.OPENCODE:
            try:
                RepositoryCache(project).ensure_exists()
            except RepositoryCacheMissingError as exc:
                raise ReviewConfigurationError(str(exc)) from exc
        return provider


class ReviewActionService:
    """Publish review comments and perform merge request state changes."""

    @staticmethod
    def _comment_marker(comment: Any) -> str:
        """Return the stable marker used to make remote comment creation idempotent."""
        return f"<!-- merge-pipeline: review-comment:{comment.pk} -->"

    @staticmethod
    @transaction.atomic
    def post_comments(review: Review) -> int:
        """Post pending comments without duplicating remote comments after retries."""
        review = Review.objects.select_related("merge_request__project__source").get(pk=review.pk)
        # Serialize publishers on the parent as well as each individual finding.
        Review.objects.select_for_update().get(pk=review.pk)
        adaptor = get_adaptor(review.merge_request.project.source)
        posted = 0
        snapshot = None
        for comment in review.comments.select_for_update().filter(external_comment_id=""):
            marker = ReviewActionService._comment_marker(comment)
            external_id = adaptor.find_comment_by_marker(
                review.merge_request.project,
                review.merge_request.external_iid,
                marker,
            )
            if external_id is not None:
                comment.external_comment_id = external_id
                comment.publication_status = "posted"
                comment.publication_error = ""
                comment.save(
                    update_fields=[
                        "external_comment_id",
                        "publication_status",
                        "publication_error",
                        "updated_at",
                    ]
                )
                continue
            if snapshot is None:
                snapshot = adaptor.fetch_review_snapshot(
                    review.merge_request.project, review.merge_request.external_iid
                )
            if not review.diff_snapshot or snapshot != review.diff_snapshot:
                comment.publication_status = "blocked"
                comment.publication_error = (
                    "Reviewed diff is stale or unavailable; run a fresh review before inline publication"
                )
                comment.save(update_fields=["publication_status", "publication_error", "updated_at"])
                continue
            try:
                position = resolve_location(
                    snapshot["changes"], comment.file_path, comment.line_number, comment.line_side
                )
            except NoCommentableLineError as exc:
                comment.publication_status = "blocked"
                comment.publication_error = str(exc)
                comment.save(update_fields=["publication_status", "publication_error", "updated_at"])
                continue
            content = comment.content
            if position["fallback"]:
                affected_file = comment.file_path.replace("`", "") or "the repository"
                content = f"**File-level finding for `{affected_file}`; anchored here because no usable specific line was provided.**\n\n{content}"
            try:
                external_id = adaptor.create_comment(
                    review.merge_request.project,
                    review.merge_request.external_iid,
                    position,
                    snapshot["revision"],
                    f"{content}\n\n{marker}",
                )
            except (AdaptorConfigurationError, AdaptorConnectionError) as exc:
                comment.anchor = position
                comment.publication_status = (
                    "blocked" if isinstance(exc, AdaptorConfigurationError) else "failed"
                )
                comment.publication_error = str(exc)
                comment.save(
                    update_fields=["anchor", "publication_status", "publication_error", "updated_at"]
                )
                continue
            comment.external_comment_id = external_id
            comment.anchor = position
            comment.publication_status = "posted"
            comment.publication_error = ""
            comment.save(
                update_fields=[
                    "external_comment_id",
                    "anchor",
                    "publication_status",
                    "publication_error",
                    "updated_at",
                ]
            )
            posted += 1
        return posted

    @staticmethod
    @transaction.atomic
    def update_mr_state(merge_request: Any, action: str) -> bool:
        """Apply a remote state transition and synchronize local state."""
        if action not in {"close", "reopen", "merge"}:
            raise ValueError("Unsupported merge request action")
        adaptor = get_adaptor(merge_request.project.source)
        if not adaptor.update_mr_state(merge_request.project, merge_request.external_iid, action):
            return False
        merge_request.state = {"close": "closed", "reopen": "opened", "merge": "merged"}[action]
        if action == "merge":
            merge_request.merged_at = timezone.now()
        elif action == "close":
            merge_request.closed_at = timezone.now()
        merge_request.save(update_fields=["state", "merged_at", "closed_at", "updated_at"])
        return True

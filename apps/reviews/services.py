"""Review action services."""

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.reviews.models import AIProvider, Review
from apps.reviews.repository_cache import RepositoryCache, RepositoryCacheMissingError
from apps.sources.factory import get_adaptor


class ReviewAlreadyRunningError(RuntimeError):
    """Raised when a merge request already has an active review."""


class ReviewConfigurationError(ValueError):
    """Raised when a merge request cannot be reviewed with current settings."""


class ReviewQueueService:
    """Queue manually requested reviews from the operations dashboard."""

    @staticmethod
    def queue_manual_review(merge_request: Any) -> str:
        """Queue one review unless the merge request already has an active one."""
        project = merge_request.project
        if not project.is_active or not project.source.is_active:
            raise ReviewConfigurationError("The project or Git source is inactive.")
        if not project.clone_url:
            raise ReviewConfigurationError("The project has no clone URL.")
        provider = project.ai_provider or AIProvider.objects.filter(is_default=True, is_active=True).first()
        if provider is None or not provider.is_active:
            raise ReviewConfigurationError("No active AI provider is configured.")
        if Review.objects.filter(
            merge_request=merge_request,
            status__in=(Review.PENDING, Review.PROCESSING),
        ).exists():
            raise ReviewAlreadyRunningError("A review is already running for this merge request.")
        if provider.provider_type == AIProvider.OPENCODE:
            try:
                RepositoryCache(project).ensure_exists()
            except RepositoryCacheMissingError as exc:
                raise ReviewConfigurationError(str(exc)) from exc
        from apps.reviews.tasks import run_manual_ai_review

        return run_manual_ai_review.delay(merge_request.pk).id


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
        adaptor = get_adaptor(review.merge_request.project.source)
        posted = 0
        for comment in review.comments.select_for_update().filter(external_comment_id=""):
            marker = ReviewActionService._comment_marker(comment)
            external_id = adaptor.find_comment_by_marker(
                review.merge_request.project,
                review.merge_request.external_iid,
                marker,
            )
            if external_id is not None:
                comment.external_comment_id = external_id
                comment.save(update_fields=["external_comment_id", "updated_at"])
                continue
            external_id = adaptor.create_comment(
                review.merge_request.project,
                review.merge_request.external_iid,
                comment.file_path,
                comment.line_number,
                f"{comment.content}\n\n{marker}",
            )
            comment.external_comment_id = external_id
            comment.save(update_fields=["external_comment_id", "updated_at"])
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

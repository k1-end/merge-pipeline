"""Review action services."""

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.reviews.models import Review
from apps.sources.factory import get_adaptor


class ReviewActionService:
    """Publish review comments and perform merge request state changes."""

    @staticmethod
    @transaction.atomic
    def post_comments(review: Review) -> int:
        """Post only comments without an external ID."""
        review = Review.objects.select_related("merge_request__project__source").get(pk=review.pk)
        adaptor = get_adaptor(review.merge_request.project.source)
        posted = 0
        for comment in review.comments.select_for_update().filter(external_comment_id=""):
            external_id = adaptor.create_comment(review.merge_request.project, review.merge_request.external_iid, comment.file_path, comment.line_number, comment.content)
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

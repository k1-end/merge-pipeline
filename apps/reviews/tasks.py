from typing import Any

from celery import shared_task


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

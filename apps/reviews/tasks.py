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

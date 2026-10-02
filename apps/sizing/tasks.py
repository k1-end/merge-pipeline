"""Celery tasks isolated on the merge-request size-labeling queue."""

from typing import Any

from celery import shared_task

from apps.sources.exceptions import AdaptorConnectionError


@shared_task(bind=True, max_retries=3, default_retry_delay=60, queue="size-labeling")
def label_merge_request_size(self: Any, merge_request_id: int, expected_sha: str) -> None:
    """Calculate and publish one merge request's managed size label."""
    from apps.sizing.models import MergeRequestSizeResult
    from apps.sizing.services import SizeLabelService, StaleSizeJobError

    try:
        SizeLabelService().label(merge_request_id, expected_sha)
    except StaleSizeJobError:
        return
    except Exception as exc:
        MergeRequestSizeResult.objects.filter(merge_request_id=merge_request_id).update(
            status=MergeRequestSizeResult.FAILED, error_message=str(exc)
        )
        if isinstance(exc, AdaptorConnectionError):
            raise self.retry(exc=exc) from exc
        raise


@shared_task(queue="size-labeling")
def queue_pending_size_labels() -> int:
    """Recover and backfill size labels for active merge requests."""
    from apps.sizing.services import queue_pending_sizes

    return queue_pending_sizes()

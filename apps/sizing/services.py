"""Size calculation, classification, and remote-label orchestration."""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.projects.models import MergeRequest
from apps.sizing.models import MergeRequestSizeResult, SizeLabelPolicy
from apps.sources.factory import get_adaptor


class StaleSizeJobError(Exception):
    """Raised when a queued job targets an obsolete source revision."""


class SizeLabelService:
    """Calculate and publish one merge request's current size label."""

    def label(self, merge_request_id: int, expected_sha: str) -> MergeRequestSizeResult | None:
        """Apply a size label unless the job or policy is obsolete."""
        policy = SizeLabelPolicy.load()
        if not policy.enabled:
            return None
        mr = MergeRequest.objects.select_related("project", "project__source").get(pk=merge_request_id)
        if mr.source_sha != expected_sha:
            raise StaleSizeJobError("Merge request source revision changed before size labeling")

        result, _ = MergeRequestSizeResult.objects.update_or_create(
            merge_request=mr,
            defaults={"status": MergeRequestSizeResult.PROCESSING, "error_message": ""},
        )
        adaptor = get_adaptor(mr.project.source)
        additions, deletions = adaptor.fetch_mr_size(mr.project, mr.external_iid)
        changed_lines = additions + deletions
        label = policy.classify(changed_lines)
        adaptor.replace_size_label(mr.project, mr.external_iid, label)

        result.source_sha = mr.source_sha
        result.additions = additions
        result.deletions = deletions
        result.changed_lines = changed_lines
        result.label = label
        result.policy_revision = policy.revision
        result.status = MergeRequestSizeResult.COMPLETED
        result.error_message = ""
        result.labeled_at = timezone.now()
        result.save()
        return result


def queue_merge_request_size(merge_request_id: int) -> bool:
    """Reserve and publish one size-label task."""
    from apps.sizing.tasks import label_merge_request_size

    with transaction.atomic():
        mr = MergeRequest.objects.select_for_update().get(pk=merge_request_id)
        policy = SizeLabelPolicy.load()
        if not policy.enabled:
            return False
        result, _ = MergeRequestSizeResult.objects.get_or_create(merge_request=mr)
        already_current = (
            result.source_sha == mr.source_sha
            and result.policy_revision == policy.revision
            and result.status
            in {
                MergeRequestSizeResult.PENDING,
                MergeRequestSizeResult.PROCESSING,
                MergeRequestSizeResult.COMPLETED,
            }
        )
        if already_current:
            return False
        result.source_sha = mr.source_sha
        result.policy_revision = policy.revision
        result.status = MergeRequestSizeResult.PENDING
        result.error_message = ""
        result.save(update_fields=["source_sha", "policy_revision", "status", "error_message", "updated_at"])
        task_args = (mr.pk, mr.source_sha)
    label_merge_request_size.delay(*task_args)
    return True


def queue_pending_sizes() -> int:
    """Queue missing, stale, or interrupted open merge-request size jobs."""
    policy = SizeLabelPolicy.load()
    if not policy.enabled:
        return 0
    stale_before = timezone.now() - timedelta(minutes=30)
    queued = 0
    merge_requests = MergeRequest.objects.filter(
        state__in=[MergeRequest.OPENED, MergeRequest.DRAFT]
    ).select_related("size_result")
    for mr in merge_requests.iterator(chunk_size=500):
        try:
            result = mr.size_result
        except MergeRequestSizeResult.DoesNotExist:
            result = None
        needs_queue = (
            result is None or result.source_sha != mr.source_sha or result.policy_revision != policy.revision
        )
        if result and result.status in {MergeRequestSizeResult.PENDING, MergeRequestSizeResult.PROCESSING}:
            interrupted = result.updated_at < stale_before
            needs_queue = needs_queue or interrupted
            if interrupted:
                result.status = MergeRequestSizeResult.FAILED
                result.save(update_fields=["status", "updated_at"])
        if needs_queue and queue_merge_request_size(mr.pk):
            queued += 1
    return queued

"""Business logic for importing repositories and merge requests."""

import logging
import re
from time import monotonic
from typing import Any, TypedDict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.projects.models import (
    MergeRequest,
    MRChange,
    MRDiscussionSyncState,
    Project,
    RemoteMRComment,
    RemoteMRReview,
)
from apps.reviews.models import ReviewComment
from apps.sources.factory import get_adaptor
from apps.sources.models import GitSource
from apps.sync.models import SyncLog

logger = logging.getLogger(__name__)


def _queue_automatic_review(merge_request_id: int) -> None:
    """Queue after commit without allowing review infrastructure to fail synchronization."""
    try:
        from apps.reviews.services import (
            ReviewAlreadyRunningError,
            ReviewConfigurationError,
            ReviewQueueService,
        )
    except Exception:  # noqa: BLE001 - synchronization has already committed successfully
        logger.exception(
            "Automatic review service could not be loaded",
            extra={"merge_request_id": merge_request_id},
        )
        return
    try:
        merge_request = MergeRequest.objects.select_related(
            "project", "project__source", "project__ai_provider"
        ).get(pk=merge_request_id)
        ReviewQueueService.queue_automatic_review(merge_request)
    except (ReviewAlreadyRunningError, ReviewConfigurationError):
        logger.info(
            "Automatic review was skipped",
            extra={"merge_request_id": merge_request_id},
            exc_info=True,
        )
    except Exception:  # noqa: BLE001 - synchronization has already committed successfully
        logger.exception(
            "Automatic review could not be queued", extra={"merge_request_id": merge_request_id}
        )


def _queue_size_label(merge_request_id: int) -> None:
    """Queue size labeling without allowing broker failures to roll back synchronization."""
    try:
        from apps.sizing.services import queue_merge_request_size

        queue_merge_request_size(merge_request_id)
    except Exception:  # noqa: BLE001 - synchronization has already committed successfully
        logger.exception(
            "Merge-request size labeling could not be queued",
            extra={"merge_request_id": merge_request_id},
        )


class SyncCounts(TypedDict):
    """Counters returned by a project synchronization."""

    new_mrs: int
    updated_mrs: int
    errors: list[str]


class SyncService:
    """Synchronize a source or one repository using its registered adaptor."""

    def sync_source(self, source: GitSource) -> dict[str, Any]:
        """Import accessible repositories and synchronize their merge requests."""
        results: dict[str, Any] = {"projects": 0, "new_mrs": 0, "updated_mrs": 0, "errors": []}
        for item in get_adaptor(source).fetch_projects():
            defaults = dict(item)
            if not defaults.get("clone_url"):
                defaults.pop("clone_url", None)
            project, _ = Project.objects.update_or_create(
                source=source, external_id=item["external_id"], defaults=defaults
            )
            results["projects"] += 1
            try:
                result = self.sync_project(project)
            except Exception as exc:  # noqa: BLE001 - isolate one repository failure
                results["errors"].append(f"{project.full_name}: {exc}")
            else:
                results["new_mrs"] += result["new_mrs"]
                results["updated_mrs"] += result["updated_mrs"]
        return results

    def sync_project(self, project: Project) -> SyncCounts:
        """Import merge requests and their changed files idempotently."""
        started = monotonic()
        log = SyncLog.objects.create(source=project.source, project=project, operation=SyncLog.INCREMENTAL)
        counts: SyncCounts = {"new_mrs": 0, "updated_mrs": 0, "errors": []}
        try:
            adaptor = get_adaptor(project.source)
            for item in adaptor.fetch_merge_requests(project, project.last_synced_at):
                defaults = {key: value for key, value in item.items() if key not in {"external_iid"}}
                with transaction.atomic():
                    existing = MergeRequest.objects.filter(
                        project=project, external_iid=item["external_iid"]
                    ).first()
                    mr, created = MergeRequest.objects.update_or_create(
                        project=project, external_iid=item["external_iid"], defaults=defaults
                    )
                    content_changed = bool(
                        existing and self._merge_request_content_changed(existing, item)
                    )
                    review_worthy_change = created or bool(
                        existing
                        and (
                            existing.source_sha != item.get("source_sha", "")
                            or existing.target_branch != item.get("target_branch", "")
                        )
                    )
                    if created or content_changed:
                        self._sync_changes(adaptor, project, mr)
                    if review_worthy_change:
                        transaction.on_commit(
                            lambda merge_request_id=mr.pk: _queue_automatic_review(merge_request_id)
                        )
                        transaction.on_commit(
                            lambda merge_request_id=mr.pk: _queue_size_label(merge_request_id)
                        )
                counts["new_mrs" if created else "updated_mrs"] += 1
            self.reconcile_active_merge_requests(project)
            discussion_mrs = MergeRequest.objects.filter(project=project).filter(
                Q(state__in=[MergeRequest.OPENED, MergeRequest.DRAFT])
                | Q(discussion_sync_state__last_full_sync_at__isnull=True)
            )
            for mr in discussion_mrs:
                try:
                    self.sync_merge_request_discussion(mr, adaptor=adaptor)
                except Exception as exc:  # noqa: BLE001 - isolate one discussion failure
                    counts["errors"].append(f"MR !{mr.external_iid} discussion: {exc}")
            project.last_synced_at = timezone.now()
            project.save(update_fields=["last_synced_at", "updated_at"])
            log.status = SyncLog.COMPLETED if not counts["errors"] else SyncLog.PARTIAL
        except Exception as exc:
            log.status = SyncLog.FAILED
            log.error_details = [str(exc)]
            raise
        finally:
            log.new_mrs = counts["new_mrs"]
            log.updated_mrs = counts["updated_mrs"]
            log.mr_count = counts["new_mrs"] + counts["updated_mrs"]
            log.error_count = len(counts["errors"])
            log.completed_at = timezone.now()
            log.duration_ms = int((monotonic() - started) * 1000)
            log.save(
                update_fields=[
                    "status",
                    "new_mrs",
                    "updated_mrs",
                    "mr_count",
                    "error_count",
                    "error_details",
                    "completed_at",
                    "duration_ms",
                    "updated_at",
                ]
            )
        return counts

    def reconcile_active_merge_requests(self, project: Project, dry_run: bool = False) -> dict[str, int]:
        """Refresh local active requests that may have closed remotely."""
        adaptor = get_adaptor(project.source)
        active = MergeRequest.objects.filter(
            project=project, state__in=[MergeRequest.OPENED, MergeRequest.DRAFT]
        ).only("id", "external_iid", "state")
        remote_open = adaptor.fetch_open_merge_request_iids(project)
        counts = {"checked": 0, "updated": 0, "unchanged": 0, "failed": 0}
        for mr in active.iterator(chunk_size=500):
            counts["checked"] += 1
            if mr.external_iid in remote_open:
                counts["unchanged"] += 1
                continue
            try:
                item = adaptor.fetch_merge_request(project, mr.external_iid)
                if item["state"] not in {MergeRequest.OPENED, MergeRequest.DRAFT}:
                    if not dry_run:
                        MergeRequest.objects.filter(pk=mr.pk).update(
                            state=item["state"],
                            external_updated_at=item["external_updated_at"],
                            merged_at=item["merged_at"],
                            closed_at=item["closed_at"],
                        )
                    counts["updated"] += 1
                else:
                    counts["unchanged"] += 1
            except Exception:  # noqa: BLE001 - one stale request must not abort the repair
                counts["failed"] += 1
        return counts

    @staticmethod
    def _merge_request_content_changed(mr: MergeRequest, item: dict) -> bool:
        """Return whether a remote update requires refreshing the file snapshot."""
        return any(
            getattr(mr, field) != item.get(field, "")
            for field in ("source_branch", "source_sha", "target_branch", "title", "description")
        )

    def _sync_changes(self, adaptor: Any, project: Project, mr: MergeRequest) -> None:
        """Replace the current file snapshot for a merge request."""
        changes = adaptor.fetch_mr_changes(project, mr.external_iid)
        MRChange.objects.filter(merge_request=mr).delete()
        MRChange.objects.bulk_create([MRChange(merge_request=mr, **change) for change in changes])

    def sync_merge_request_discussion(self, mr: MergeRequest, adaptor: Any | None = None) -> None:
        """Synchronize one complete remote discussion snapshot."""
        adaptor = adaptor or get_adaptor(mr.project.source)
        state, _ = MRDiscussionSyncState.objects.get_or_create(merge_request=mr)
        seen_at = timezone.now()
        try:
            comments = adaptor.fetch_mr_comments(mr.project, mr.external_iid)
            reviews = adaptor.fetch_mr_reviews(mr.project, mr.external_iid)
            self._replace_remote_discussion_snapshot(mr, comments, reviews, seen_at)
        except Exception as exc:
            state.error_message = str(exc)
            state.save(update_fields=["error_message", "updated_at"])
            raise
        state.last_successful_sync_at = seen_at
        state.last_full_sync_at = seen_at
        state.error_message = ""
        state.save(
            update_fields=["last_successful_sync_at", "last_full_sync_at", "error_message", "updated_at"]
        )

    @staticmethod
    @transaction.atomic
    def _replace_remote_discussion_snapshot(
        mr: MergeRequest, comments: list[dict], reviews: list[dict], seen_at: Any
    ) -> None:
        """Upsert a complete snapshot and tombstone records no longer returned."""
        external_ids = [comment["external_id"] for comment in comments]
        local_by_external_id = {
            comment.external_comment_id: comment
            for comment in ReviewComment.objects.filter(
                review__merge_request=mr, external_comment_id__in=external_ids
            )
        }
        marker_ids = {
            int(match.group(1))
            for comment in comments
            if (match := re.search(r"<!-- merge-pipeline: review-comment:(\d+) -->", comment["body"]))
        }
        local_by_pk = {
            comment.pk: comment
            for comment in ReviewComment.objects.filter(review__merge_request=mr, pk__in=marker_ids)
        }
        seen_comment_keys = []
        for item in comments:
            defaults = dict(item)
            external_id = str(defaults.pop("external_id"))
            provider_kind = defaults.pop("provider_kind")
            marker = re.search(r"<!-- merge-pipeline: review-comment:(\d+) -->", defaults["body"])
            local_comment = (
                local_by_external_id.get(external_id)
                if provider_kind
                in {RemoteMRComment.GITHUB_ISSUE_COMMENT, RemoteMRComment.GITLAB_NOTE}
                else None
            )
            if local_comment is None and marker:
                local_comment = local_by_pk.get(int(marker.group(1)))
            defaults.update(
                {"local_review_comment": local_comment, "last_seen_at": seen_at, "deleted_at": None}
            )
            RemoteMRComment.objects.update_or_create(
                merge_request=mr,
                provider_kind=provider_kind,
                external_id=external_id,
                defaults=defaults,
            )
            seen_comment_keys.append((provider_kind, external_id))

        existing_comments = RemoteMRComment.objects.filter(merge_request=mr, deleted_at__isnull=True)
        if seen_comment_keys:
            seen_query = Q()
            for provider_kind, external_id in seen_comment_keys:
                seen_query |= Q(provider_kind=provider_kind, external_id=external_id)
            existing_comments.exclude(seen_query).update(deleted_at=seen_at)
        else:
            existing_comments.update(deleted_at=seen_at)

        seen_review_ids = []
        for item in reviews:
            defaults = dict(item)
            external_id = str(defaults.pop("external_id"))
            defaults.update({"last_seen_at": seen_at, "deleted_at": None})
            RemoteMRReview.objects.update_or_create(
                merge_request=mr, external_id=external_id, defaults=defaults
            )
            seen_review_ids.append(external_id)
        existing_reviews = RemoteMRReview.objects.filter(merge_request=mr, deleted_at__isnull=True)
        if seen_review_ids:
            existing_reviews.exclude(external_id__in=seen_review_ids).update(deleted_at=seen_at)
        else:
            existing_reviews.update(deleted_at=seen_at)

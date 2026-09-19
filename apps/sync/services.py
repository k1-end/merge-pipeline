"""Business logic for importing repositories and merge requests."""

from time import monotonic
from typing import Any, TypedDict

from django.db import transaction
from django.utils import timezone

from apps.projects.models import MergeRequest, MRChange, Project
from apps.sources.factory import get_adaptor
from apps.sources.models import GitSource
from apps.sync.models import SyncLog


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
                    mr, created = MergeRequest.objects.update_or_create(
                        project=project, external_iid=item["external_iid"], defaults=defaults
                    )
                    self._sync_changes(adaptor, project, mr)
                counts["new_mrs" if created else "updated_mrs"] += 1
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
            log.save(update_fields=["status", "new_mrs", "updated_mrs", "mr_count", "error_count", "error_details", "completed_at", "duration_ms", "updated_at"])
        return counts

    def _sync_changes(self, adaptor: Any, project: Project, mr: MergeRequest) -> None:
        """Replace the current file snapshot for a merge request."""
        changes = adaptor.fetch_mr_changes(project, mr.external_iid)
        MRChange.objects.filter(merge_request=mr).delete()
        MRChange.objects.bulk_create([MRChange(merge_request=mr, **change) for change in changes])

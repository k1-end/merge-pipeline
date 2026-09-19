"""Celery tasks for source synchronization and webhook processing."""

from datetime import timedelta
from typing import Any

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.sources.exceptions import AdaptorConfigurationError


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_project(self: Any, project_id: int) -> Any:
    """Synchronize one active project, retrying transient failures."""
    from apps.projects.models import Project
    from apps.sync.services import SyncService

    project = Project.objects.select_related("source", "source__credential").get(id=project_id)
    try:
        return SyncService().sync_project(project)
    except AdaptorConfigurationError:
        raise
    except Exception as exc:  # noqa: BLE001 - Celery owns retry policy
        raise self.retry(exc=exc) from exc


@shared_task
def sync_all_projects() -> int:
    """Queue incremental synchronization for all active projects."""
    from apps.projects.models import Project

    ids = list(Project.objects.filter(is_active=True, source__is_active=True, source__sync_enabled=True).values_list("id", flat=True))
    for project_id in ids:
        sync_project.delay(project_id)
    return len(ids)


@shared_task
def sync_stale_projects() -> int:
    """Queue projects that have not synchronized in the last six hours."""
    from apps.projects.models import Project

    cutoff = timezone.now() - timedelta(hours=6)
    ids = list(
        Project.objects.filter(
            is_active=True,
            source__is_active=True,
            source__sync_enabled=True,
        )
        .filter(Q(last_synced_at__isnull=True) | Q(last_synced_at__lt=cutoff))
        .values_list("id", flat=True)
    )
    for project_id in ids:
        sync_project.delay(project_id)
    return len(ids)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def process_webhook_event(self: Any, event_id: int) -> None:
    """Process one persisted webhook event asynchronously."""
    from apps.sync.models import WebhookEvent
    from apps.sync.webhook_processor import WebhookProcessor

    event = WebhookEvent.objects.select_related("source").get(id=event_id)
    try:
        WebhookProcessor().process(event)
    except Exception as exc:  # noqa: BLE001 - Celery owns retry policy
        raise self.retry(exc=exc) from exc


@shared_task
def cleanup_webhook_events(days: int = 30) -> int:
    """Delete processed webhook payloads older than the retention period."""
    from apps.sync.models import WebhookEvent

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = WebhookEvent.objects.filter(processed=True, received_at__lt=cutoff).delete()
    return deleted

"""Idempotent processing of provider webhook events."""

from django.db import transaction
from django.utils import timezone

from apps.projects.models import MergeRequest, Project
from apps.sources.factory import get_adaptor
from apps.sync.models import SyncLog, WebhookEvent


class WebhookProcessor:
    """Resolve webhook references and refresh the affected merge request."""

    @transaction.atomic
    def process(self, event: WebhookEvent) -> None:
        """Process an event once; duplicate deliveries are harmless."""
        event = WebhookEvent.objects.select_for_update().select_related("source").get(pk=event.pk)
        if event.processed:
            return
        try:
            parsed = get_adaptor(event.source).parse_webhook(event.payload, event.event_type)
            project = Project.objects.get(source=event.source, external_id=parsed["external_project_id"])
            if parsed.get("external_iid"):
                mr = MergeRequest.objects.filter(project=project, external_iid=parsed["external_iid"]).first()
                if mr is None or (
                    parsed.get("external_updated_at")
                    and parsed["external_updated_at"] > mr.external_updated_at
                ):
                    from apps.sync.services import SyncService

                    SyncService().sync_project(project)
            event.event_action = parsed.get("action", event.event_action)
            event.processed = True
            event.processed_at = timezone.now()
            event.save(update_fields=["event_action", "processed", "processed_at", "updated_at"])
            SyncLog.objects.create(source=event.source, project=project, operation=SyncLog.WEBHOOK, status=SyncLog.COMPLETED, completed_at=timezone.now())
        except Exception as exc:
            event.error_message = str(exc)
            event.save(update_fields=["error_message", "updated_at"])
            raise

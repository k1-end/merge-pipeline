"""Synchronization and webhook audit models."""

from django.db import models

from apps.core.models import TimestampedModel


class SyncLog(TimestampedModel):
    """Audit record for a source or project synchronization."""

    FULL = "full_sync"
    INCREMENTAL = "incremental_sync"
    WEBHOOK = "webhook_process"
    STATE_CHANGE = "mr_state_change"
    OPERATIONS = ((FULL, "Full Sync"), (INCREMENTAL, "Incremental Sync"), (WEBHOOK, "Webhook Processing"), (STATE_CHANGE, "MR State Change"))
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    STATUSES = ((STARTED, "Started"), (COMPLETED, "Completed"), (FAILED, "Failed"), (PARTIAL, "Partial"))

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="sync_logs", null=True, blank=True)
    source = models.ForeignKey("sources.GitSource", on_delete=models.CASCADE, related_name="sync_logs")
    operation = models.CharField(max_length=30, choices=OPERATIONS)
    status = models.CharField(max_length=20, choices=STATUSES, default=STARTED)
    mr_count = models.PositiveIntegerField(default=0)
    new_mrs = models.PositiveIntegerField(default=0)
    updated_mrs = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    error_details = models.JSONField(default=list)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        """Database mapping and synchronization lookup indexes."""

        db_table = "sync_logs"
        indexes = [models.Index(fields=["source", "-created_at"]), models.Index(fields=["project", "-created_at"])]


class WebhookEvent(TimestampedModel):
    """Persisted webhook payload awaiting asynchronous processing."""

    source = models.ForeignKey("sources.GitSource", on_delete=models.CASCADE, related_name="webhook_events")
    event_type = models.CharField(max_length=100)
    event_action = models.CharField(max_length=50, blank=True)
    payload = models.JSONField()
    headers = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Database mapping and webhook processing indexes."""

        db_table = "webhook_events"
        indexes = [models.Index(fields=["source", "processed", "-received_at"]), models.Index(fields=["event_type", "-received_at"])]

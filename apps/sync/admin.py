from django.contrib import admin

from apps.sync.models import SyncLog, WebhookEvent


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    """Admin configuration for synchronization audit records."""

    list_display = ("source", "project", "operation", "status", "mr_count", "duration_ms", "created_at")
    list_filter = ("operation", "status", "source")
    search_fields = ("source__name", "project__full_name")
    readonly_fields = ("created_at", "updated_at", "completed_at", "duration_ms")
    date_hierarchy = "created_at"


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """Admin configuration for received webhook payloads."""

    list_display = ("source", "event_type", "event_action", "processed", "received_at")
    list_filter = ("event_type", "processed", "source")
    search_fields = ("event_type", "event_action", "error_message")
    readonly_fields = ("source", "event_type", "event_action", "payload", "headers", "received_at", "processed_at")
    date_hierarchy = "received_at"

from typing import Any

from django.contrib import admin

from apps.projects.models import MergeRequest, MRChange, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Admin configuration for monitored repositories."""

    list_display = ("full_name", "source", "is_active", "auto_review_enabled", "last_synced_at")
    list_filter = ("source", "source__source_type", "is_active", "auto_review_enabled")
    search_fields = ("name", "full_name", "clone_url")
    readonly_fields = ("created_at", "updated_at", "last_synced_at")
    raw_id_fields = ("source", "ai_provider")
    actions = ("enable_auto_review", "disable_auto_review", "trigger_sync")

    @admin.action(description="Enable auto-review")
    def enable_auto_review(self, request: Any, queryset: Any) -> None:
        """Enable automatic reviews for selected repositories."""
        queryset.update(auto_review_enabled=True)

    @admin.action(description="Disable auto-review")
    def disable_auto_review(self, request: Any, queryset: Any) -> None:
        """Disable automatic reviews for selected repositories."""
        queryset.update(auto_review_enabled=False)

    @admin.action(description="Trigger sync now")
    def trigger_sync(self, request: Any, queryset: Any) -> None:
        """Queue synchronization for selected repositories."""
        from apps.sync.tasks import sync_project

        for project_id in queryset.values_list("id", flat=True):
            sync_project.delay(project_id)


@admin.register(MergeRequest)
class MergeRequestAdmin(admin.ModelAdmin):
    """Admin configuration for imported merge requests."""

    list_display = ("external_iid", "title", "project", "state", "external_updated_at", "last_synced_at")
    list_filter = ("state", "project__source", "project")
    search_fields = ("title", "external_iid", "author_username", "source_branch")
    readonly_fields = ("created_at", "updated_at", "last_synced_at")
    raw_id_fields = ("project",)
    date_hierarchy = "external_updated_at"


@admin.register(MRChange)
class MRChangeAdmin(admin.ModelAdmin):
    """Admin configuration for changed-file snapshots."""

    list_display = ("file_path", "change_type", "merge_request", "created_at")
    list_filter = ("change_type",)
    search_fields = ("file_path", "merge_request__title")
    raw_id_fields = ("merge_request",)

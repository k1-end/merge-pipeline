from typing import Any

from django.contrib import admin

from apps.projects.models import MergeRequest, MRChange, Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Admin configuration for monitored repositories."""

    list_display = (
        "full_name",
        "source",
        "is_active",
        "auto_review_enabled",
        "cache_status",
        "last_synced_at",
    )
    list_filter = ("source", "source__source_type", "is_active", "auto_review_enabled", "cache_status")
    search_fields = ("name", "full_name", "clone_url")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_synced_at",
        "cache_status",
        "cache_error",
        "cache_updated_at",
    )
    raw_id_fields = ("source", "ai_provider")
    actions = ("enable_auto_review", "disable_auto_review", "trigger_sync", "trigger_cache_clone")

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

    @admin.action(description="Clone repository cache")
    def trigger_cache_clone(self, request: Any, queryset: Any) -> None:
        """Queue persistent repository cloning for selected projects."""
        from apps.reviews.tasks import clone_repository_cache

        for project in queryset:
            project.cache_status = Project.CACHE_CLONING
            project.cache_error = ""
            project.save(update_fields=["cache_status", "cache_error", "updated_at"])
            try:
                clone_repository_cache.delay(project.pk)
            except Exception as exc:  # noqa: BLE001 - show broker failures in project state
                project.cache_status = Project.CACHE_ERROR
                project.cache_error = str(exc)
                project.save(update_fields=["cache_status", "cache_error", "updated_at"])


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

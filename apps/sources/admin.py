from typing import Any

from django.contrib import admin, messages

from apps.sources.exceptions import AdaptorError
from apps.sources.factory import get_adaptor
from apps.sources.models import GitSource


@admin.register(GitSource)
class GitSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "source_type", "base_url", "is_active", "sync_enabled")
    list_filter = ("source_type", "is_active", "sync_enabled")
    search_fields = ("name", "base_url")
    readonly_fields = ("created_at", "updated_at")
    actions = ("test_connection",)

    @admin.action(description="Test connection")
    def test_connection(self, request: Any, queryset: Any) -> None:
        for source in queryset.select_related("credential"):
            try:
                get_adaptor(source).test_connection()
            except AdaptorError:
                self.message_user(request, f"{source.name}: connection failed", messages.ERROR)
            else:
                self.message_user(request, f"{source.name}: connection succeeded", messages.SUCCESS)

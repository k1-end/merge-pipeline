from typing import Any

from django import forms
from django.contrib import admin
from django.http import HttpRequest

from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.reviews.services import ReviewActionService


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    """Admin configuration for AI provider settings."""

    list_display = ("name", "provider_type", "model_name", "is_active", "is_default")
    list_filter = ("provider_type", "is_active", "is_default")
    search_fields = ("name", "model_name")
    readonly_fields = ("created_at", "updated_at")
    exclude = ("api_key_encrypted",)

    def get_form(self, request: HttpRequest, obj: Any = None, change: bool = False, **kwargs: Any) -> type[forms.ModelForm]:
        """Add a write-only API key field without exposing stored ciphertext."""
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["api_key"] = forms.CharField(required=not bool(obj), widget=forms.PasswordInput)
        return form

    def save_model(self, request: HttpRequest, obj: AIProvider, form: forms.ModelForm, change: bool) -> None:
        """Encrypt a newly supplied API key before saving."""
        from apps.credentials.services import CredentialEncryption

        key = form.cleaned_data.get("api_key")
        if key:
            obj.api_key_encrypted = CredentialEncryption().encrypt(key)
        super().save_model(request, obj, form, change)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    """Admin configuration for review executions."""

    list_display = ("merge_request", "provider", "status", "cost_usd", "duration_ms", "created_at")
    list_filter = ("status", "provider")
    search_fields = ("merge_request__title", "merge_request__external_iid")
    readonly_fields = ("created_at", "updated_at", "completed_at", "input_tokens", "output_tokens", "cost_usd", "duration_ms")
    raw_id_fields = ("merge_request", "provider")
    actions = ("post_comments",)

    @admin.action(description="Post pending review comments")
    def post_comments(self, request: Any, queryset: Any) -> None:
        """Publish pending comments for completed reviews."""
        for review in queryset.filter(status=Review.COMPLETED):
            ReviewActionService.post_comments(review)


@admin.register(ReviewComment)
class ReviewCommentAdmin(admin.ModelAdmin):
    """Admin configuration for review findings."""

    list_display = ("file_path", "line_number", "severity", "comment_type", "is_resolved", "review")
    list_filter = ("comment_type", "severity", "is_resolved")
    search_fields = ("file_path", "content", "external_comment_id")
    readonly_fields = ("created_at", "updated_at", "external_comment_id")
    raw_id_fields = ("review",)

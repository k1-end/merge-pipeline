from typing import Any

from django import forms
from django.contrib import admin

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption


class CredentialAdminForm(forms.ModelForm):
    access_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    private_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))

    class Meta:
        model = GitSourceCredential
        fields = ("source", "access_token", "app_id", "private_key")

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["access_token"].help_text = "Leave blank to keep the existing token."
            self.fields["private_key"].help_text = "Leave blank to keep the existing private key."

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean() or {}
        if not self.instance.pk and not cleaned_data.get("access_token"):
            self.add_error("access_token", "A token is required when creating credentials.")
        return cleaned_data

    def save(self, commit: bool = True) -> GitSourceCredential:
        instance = super().save(commit=False)
        cipher = CredentialEncryption()
        token = self.cleaned_data.get("access_token")
        private_key = self.cleaned_data.get("private_key")
        if token:
            instance.access_token_encrypted = cipher.encrypt(token)
        if private_key:
            instance.private_key_encrypted = cipher.encrypt(private_key)
        if commit:
            instance.save()
        return instance


@admin.register(GitSourceCredential)
class GitSourceCredentialAdmin(admin.ModelAdmin):
    form = CredentialAdminForm
    list_display = ("source", "has_token", "has_private_key", "updated_at")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="Has token")
    def has_token(self, obj: GitSourceCredential) -> bool:
        return bool(obj.access_token_encrypted)

    @admin.display(boolean=True, description="Has private key")
    def has_private_key(self, obj: GitSourceCredential) -> bool:
        return bool(obj.private_key_encrypted)

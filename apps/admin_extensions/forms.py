from typing import Any

from django import forms
from django.db import transaction

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.projects.models import Project
from apps.reviews.models import AIProvider
from apps.sizing.models import SizeLabelPolicy
from apps.sources.models import GitSource


class ProjectForm(forms.ModelForm):
    """Edit a monitored project from the operations panel."""

    class Meta:
        """Expose the project fields that are writable in Django admin."""

        model = Project
        fields = (
            "source",
            "external_id",
            "name",
            "full_name",
            "web_url",
            "clone_url",
            "default_branch",
            "is_active",
            "auto_review_enabled",
            "review_on_draft",
            "ai_provider",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Apply dashboard-compatible styling to each project field."""
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"


class SourceForm(forms.ModelForm):
    """Edit a git source and its write-only credential fields together."""

    access_token = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))
    private_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))

    class Meta:
        """Configure source fields exposed in the panel."""

        model = GitSource
        fields = ("name", "source_type", "base_url", "is_active", "sync_enabled", "webhook_secret")
        widgets = {"webhook_secret": forms.PasswordInput(render_value=False)}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Add guidance when editing existing credentials."""
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["access_token"].help_text = "Leave blank to keep the existing token."
            self.fields["private_key"].help_text = "Leave blank to keep the existing private key."

    @transaction.atomic
    def save(self, commit: bool = True) -> GitSource:
        """Save the source and encrypt any newly supplied secrets."""
        source = super().save(commit=commit)
        token = self.cleaned_data.get("access_token")
        private_key = self.cleaned_data.get("private_key")
        if commit and (token or private_key):
            credential, _ = GitSourceCredential.objects.get_or_create(
                source=source,
                defaults={"access_token_encrypted": b""},
            )
            cipher = CredentialEncryption()
            if token:
                credential.access_token_encrypted = cipher.encrypt(token)
            if private_key:
                credential.private_key_encrypted = cipher.encrypt(private_key)
            credential.save()
        return source


class ProviderForm(forms.ModelForm):
    """Edit an AI provider without displaying its stored API key."""

    api_key = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False))

    class Meta:
        """Configure provider fields exposed in the panel."""

        model = AIProvider
        fields = (
            "name",
            "provider_type",
            "api_base_url",
            "model_name",
            "max_tokens_per_request",
            "temperature",
            "input_cost_per_million",
            "output_cost_per_million",
            "is_active",
            "is_default",
            "system_prompt",
            "review_prompt_template",
        )
        widgets = {
            "system_prompt": forms.Textarea(attrs={"rows": 4}),
            "review_prompt_template": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Add guidance when editing an existing API key."""
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["api_key"].help_text = "Leave blank to keep the existing API key."

    @transaction.atomic
    def save(self, commit: bool = True) -> AIProvider:
        """Save the provider and encrypt any newly supplied API key."""
        provider = super().save(commit=commit)
        api_key = self.cleaned_data.get("api_key")
        if commit and api_key:
            provider.api_key_encrypted = CredentialEncryption().encrypt(api_key)
            provider.save(update_fields=["api_key_encrypted", "updated_at"])
        return provider


class SizeLabelPolicyForm(forms.ModelForm):
    """Edit the global merge-request size thresholds."""

    class Meta:
        """Expose the policy fields editable from the dashboard."""

        model = SizeLabelPolicy
        fields = ("enabled", "xs_max", "s_max", "m_max", "l_max")
        labels = {
            "xs_max": "XS maximum",
            "s_max": "S maximum",
            "m_max": "M maximum",
            "l_max": "L maximum",
        }
        help_texts = {
            "l_max": "Changes above this value are labeled size/XL.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Apply dashboard-compatible styling."""
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"

    def save(self, commit: bool = True) -> SizeLabelPolicy:
        """Increment the revision whenever policy inputs change."""
        policy = super().save(commit=False)
        if self.changed_data:
            policy.revision += 1
        if commit:
            policy.save()
        return policy

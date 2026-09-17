"""Encrypted credentials for remote integrations."""

from django.db import models

from apps.core.models import TimestampedModel


class GitSourceCredential(TimestampedModel):
    """Encrypted access credentials belonging to one git source."""

    source = models.OneToOneField(
        "sources.GitSource", on_delete=models.CASCADE, related_name="credential"
    )
    access_token_encrypted = models.BinaryField()
    app_id = models.CharField(max_length=100, blank=True)
    private_key_encrypted = models.BinaryField(blank=True)

    class Meta:
        """Database mapping for encrypted source credentials."""

        db_table = "git_source_credentials"

    def __str__(self) -> str:
        """Return a non-sensitive display label."""
        return f"Credentials for {self.source}"

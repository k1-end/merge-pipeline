"""Models describing remote git hosts."""

from django.db import models

from apps.core.models import IsActiveModel, TimestampedModel


class GitSource(IsActiveModel, TimestampedModel):
    """A configured GitLab or GitHub host."""

    GITLAB = "gitlab"
    GITHUB = "github"
    SOURCE_TYPES = ((GITLAB, "GitLab"), (GITHUB, "GitHub"))

    name = models.CharField(max_length=100, unique=True)
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPES)
    base_url = models.URLField(help_text="API base URL, for example https://gitlab.com/api/v4")
    sync_enabled = models.BooleanField(default=True)
    webhook_secret = models.CharField(max_length=255, blank=True)

    class Meta:
        """Database mapping and source lookup indexes."""

        db_table = "git_sources"
        indexes = [models.Index(fields=["source_type", "is_active"])]

    def __str__(self) -> str:
        """Return the configured source name."""
        return self.name

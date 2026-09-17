"""Repository and merge request models."""

from django.db import models

from apps.core.models import TimestampedModel


class Project(TimestampedModel):
    """A repository monitored from exactly one remote source."""

    source = models.ForeignKey("sources.GitSource", on_delete=models.CASCADE, related_name="projects")
    external_id = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    full_name = models.CharField(max_length=500)
    web_url = models.URLField()
    clone_url = models.URLField(blank=True, null=True)
    default_branch = models.CharField(max_length=100, default="main")
    is_active = models.BooleanField(default=True)
    auto_review_enabled = models.BooleanField(default=False)
    review_on_draft = models.BooleanField(default=False)
    ai_provider = models.ForeignKey("reviews.AIProvider", on_delete=models.SET_NULL, null=True, blank=True, related_name="projects")
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Database mapping and repository lookup indexes."""

        db_table = "projects"
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="uniq_project_source_external_id"),
            models.UniqueConstraint(fields=["clone_url"], name="uniq_project_clone_url"),
        ]
        indexes = [
            models.Index(fields=["source", "is_active"]),
            models.Index(fields=["-last_synced_at"]),
        ]

    def __str__(self) -> str:
        """Return the repository's namespace-qualified name."""
        return self.full_name


class MergeRequest(TimestampedModel):
    """A merge request or pull request imported from a project."""

    OPENED = "opened"
    CLOSED = "closed"
    MERGED = "merged"
    DRAFT = "draft"
    STATE_CHOICES = ((OPENED, "Opened"), (CLOSED, "Closed"), (MERGED, "Merged"), (DRAFT, "Draft"))

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="merge_requests")
    external_id = models.CharField(max_length=100)
    external_iid = models.CharField(max_length=100)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    state = models.CharField(max_length=20, choices=STATE_CHOICES)
    author_username = models.CharField(max_length=255)
    author_external_id = models.CharField(max_length=100, blank=True)
    source_branch = models.CharField(max_length=255)
    target_branch = models.CharField(max_length=255)
    web_url = models.URLField()
    opened_at = models.DateTimeField()
    external_updated_at = models.DateTimeField()
    merged_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Database mapping and merge request lookup indexes."""

        db_table = "merge_requests"
        constraints = [
            models.UniqueConstraint(fields=["project", "external_iid"], name="uniq_mr_project_external_iid")
        ]
        indexes = [
            models.Index(fields=["project", "state"]),
            models.Index(fields=["-external_updated_at"]),
            models.Index(fields=["author_username"]),
        ]

    def __str__(self) -> str:
        """Return the external identifier and title."""
        return f"!{self.external_iid} {self.title}"


class MRChange(TimestampedModel):
    """A changed file and unified diff belonging to a merge request."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    CHANGE_TYPES = ((ADDED, "Added"), (MODIFIED, "Modified"), (DELETED, "Deleted"), (RENAMED, "Renamed"))

    merge_request = models.ForeignKey(MergeRequest, on_delete=models.CASCADE, related_name="changes")
    file_path = models.CharField(max_length=1000)
    old_path = models.CharField(max_length=1000, blank=True)
    change_type = models.CharField(max_length=20, choices=CHANGE_TYPES)
    diff = models.TextField(blank=True)

    class Meta:
        """Database mapping and merge request index."""

        db_table = "mr_changes"
        indexes = [models.Index(fields=["merge_request"])]

"""Repository and merge request models."""

from django.db import models

from apps.core.models import TimestampedModel


class Project(TimestampedModel):
    """A repository monitored from exactly one remote source."""

    CACHE_MISSING = "missing"
    CACHE_CLONING = "cloning"
    CACHE_READY = "ready"
    CACHE_ERROR = "error"
    CACHE_STATUS_CHOICES = (
        (CACHE_MISSING, "Missing"),
        (CACHE_CLONING, "Cloning"),
        (CACHE_READY, "Ready"),
        (CACHE_ERROR, "Error"),
    )

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
    ai_provider = models.ForeignKey(
        "reviews.AIProvider", on_delete=models.SET_NULL, null=True, blank=True, related_name="projects"
    )
    cache_status = models.CharField(max_length=20, choices=CACHE_STATUS_CHOICES, default=CACHE_MISSING)
    cache_error = models.TextField(blank=True)
    cache_updated_at = models.DateTimeField(null=True, blank=True)
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
        ordering = ["-external_updated_at", "-id"]
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


class RemoteMRReview(TimestampedModel):
    """A provider review summary such as a GitHub approval."""

    merge_request = models.ForeignKey(MergeRequest, on_delete=models.CASCADE, related_name="remote_reviews")
    external_id = models.CharField(max_length=100)
    author_username = models.CharField(max_length=255)
    author_external_id = models.CharField(max_length=100, blank=True)
    body = models.TextField(blank=True)
    state = models.CharField(max_length=30)
    commit_sha = models.CharField(max_length=64, blank=True)
    web_url = models.URLField(blank=True)
    provider_created_at = models.DateTimeField(null=True, blank=True)
    provider_updated_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Database mapping and provider identity constraint."""

        db_table = "remote_mr_reviews"
        ordering = ["provider_created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["merge_request", "external_id"], name="uniq_remote_review_mr_external_id"
            )
        ]
        indexes = [models.Index(fields=["merge_request", "deleted_at"])]


class RemoteMRComment(TimestampedModel):
    """A comment or threaded note synchronized from the remote provider."""

    GITHUB_ISSUE_COMMENT = "github_issue_comment"
    GITHUB_REVIEW_COMMENT = "github_review_comment"
    GITLAB_NOTE = "gitlab_note"
    PROVIDER_KINDS = (
        (GITHUB_ISSUE_COMMENT, "GitHub conversation comment"),
        (GITHUB_REVIEW_COMMENT, "GitHub review comment"),
        (GITLAB_NOTE, "GitLab note"),
    )
    GENERAL = "general"
    INLINE = "inline"
    CATEGORIES = ((GENERAL, "General"), (INLINE, "Inline"))

    merge_request = models.ForeignKey(MergeRequest, on_delete=models.CASCADE, related_name="remote_comments")
    local_review_comment = models.ForeignKey(
        "reviews.ReviewComment",
        on_delete=models.SET_NULL,
        related_name="remote_copies",
        null=True,
        blank=True,
    )
    provider_kind = models.CharField(max_length=40, choices=PROVIDER_KINDS)
    category = models.CharField(max_length=20, choices=CATEGORIES, default=GENERAL)
    external_id = models.CharField(max_length=100)
    external_thread_id = models.CharField(max_length=255, blank=True)
    external_review_id = models.CharField(max_length=100, blank=True)
    parent_external_id = models.CharField(max_length=100, blank=True)
    author_username = models.CharField(max_length=255)
    author_external_id = models.CharField(max_length=100, blank=True)
    body = models.TextField(blank=True)
    web_url = models.URLField(blank=True)
    file_path = models.CharField(max_length=1000, blank=True)
    line_number = models.PositiveIntegerField(null=True, blank=True)
    start_line_number = models.PositiveIntegerField(null=True, blank=True)
    line_side = models.CharField(max_length=10, blank=True)
    commit_sha = models.CharField(max_length=64, blank=True)
    is_resolvable = models.BooleanField(default=False)
    is_resolved = models.BooleanField(default=False)
    provider_created_at = models.DateTimeField(null=True, blank=True)
    provider_updated_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField()
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Database mapping and provider identity constraint."""

        db_table = "remote_mr_comments"
        ordering = ["provider_created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["merge_request", "provider_kind", "external_id"],
                name="uniq_remote_comment_mr_kind_external_id",
            )
        ]
        indexes = [
            models.Index(fields=["merge_request", "deleted_at"]),
            models.Index(fields=["external_thread_id"]),
        ]


class MRDiscussionSyncState(TimestampedModel):
    """Synchronization status for one merge request's remote discussion."""

    merge_request = models.OneToOneField(
        MergeRequest, on_delete=models.CASCADE, related_name="discussion_sync_state"
    )
    last_successful_sync_at = models.DateTimeField(null=True, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        """Database mapping for discussion synchronization state."""

        db_table = "mr_discussion_sync_states"

"""AI provider and review result models."""

from django.db import models

from apps.core.models import TimestampedModel


class AIProvider(TimestampedModel):
    """Configured provider used to review merge requests."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENCODE = "opencode"
    OLLAMA = "ollama"
    VLLM = "vllm"
    CUSTOM = "custom"
    PROVIDER_TYPES = (
        (ANTHROPIC, "Anthropic (Claude)"),
        (OPENAI, "OpenAI"),
        (OPENCODE, "OpenCode Agent"),
        (OLLAMA, "Ollama (Local)"),
        (VLLM, "vLLM (Local)"),
        (CUSTOM, "Custom HTTP API"),
    )
    name = models.CharField(max_length=100, unique=True)
    provider_type = models.CharField(max_length=20, choices=PROVIDER_TYPES)
    api_base_url = models.URLField(blank=True)
    model_name = models.CharField(max_length=100)
    api_key_encrypted = models.BinaryField(blank=True, default=b"")
    max_tokens_per_request = models.PositiveIntegerField(default=8000)
    temperature = models.FloatField(default=0.1)
    input_cost_per_million = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    output_cost_per_million = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    system_prompt = models.TextField(blank=True)
    review_prompt_template = models.TextField(blank=True)

    class Meta:
        """Database mapping for provider configuration."""

        db_table = "ai_providers"

    def __str__(self) -> str:
        """Return a non-sensitive provider label."""
        return f"{self.name} ({self.model_name})"


class Review(TimestampedModel):
    """One AI review execution for a merge request."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STATUSES = (
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
    )
    merge_request = models.ForeignKey(
        "projects.MergeRequest", on_delete=models.CASCADE, related_name="reviews"
    )
    provider = models.ForeignKey(AIProvider, on_delete=models.PROTECT, related_name="reviews")
    status = models.CharField(max_length=20, choices=STATUSES, default=PENDING)
    summary = models.TextField(blank=True)
    findings = models.JSONField(default=list)
    raw_response = models.JSONField(null=True, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    celery_task_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    source_branch = models.CharField(max_length=255, blank=True)
    target_branch = models.CharField(max_length=255, blank=True)
    base_sha = models.CharField(max_length=64, blank=True)
    head_sha = models.CharField(max_length=64, blank=True)
    merge_base_sha = models.CharField(max_length=64, blank=True)

    class Meta:
        """Database mapping and review lookup indexes."""

        db_table = "reviews"
        indexes = [
            models.Index(fields=["merge_request", "-created_at"]),
            models.Index(fields=["provider", "status"]),
        ]


class ReviewComment(TimestampedModel):
    """A structured finding associated with a changed file and line."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="comments")
    file_path = models.CharField(max_length=1000)
    line_number = models.PositiveIntegerField(null=True, blank=True)
    comment_type = models.CharField(
        max_length=20,
        choices=(
            ("issue", "Issue"),
            ("suggestion", "Suggestion"),
            ("praise", "Praise"),
            ("question", "Question"),
        ),
    )
    severity = models.CharField(max_length=20, blank=True)
    content = models.TextField()
    external_comment_id = models.CharField(max_length=100, blank=True)
    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=255, blank=True)

    class Meta:
        """Database mapping for review comments."""

        db_table = "review_comments"
        indexes = [models.Index(fields=["review"]), models.Index(fields=["external_comment_id"])]


class ReviewExecutionLogChunk(TimestampedModel):
    """An ordered, lossless chunk of provider output for one review execution."""

    SYSTEM = "system"
    STDOUT = "stdout"
    STDERR = "stderr"
    PROVIDER = "provider"
    STREAMS = (
        (SYSTEM, "System"),
        (STDOUT, "Standard output"),
        (STDERR, "Standard error"),
        (PROVIDER, "Provider response"),
    )
    CHUNK_SIZE = 64 * 1024

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="execution_log_chunks")
    sequence = models.PositiveBigIntegerField()
    stream = models.CharField(max_length=20, choices=STREAMS)
    content = models.BinaryField()

    class Meta:
        """Keep chunks in their original write order without imposing a total size limit."""

        db_table = "review_execution_log_chunks"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(fields=["review", "sequence"], name="review_log_chunk_sequence_unique")
        ]

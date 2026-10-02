"""Persistent configuration and execution state for size labels."""

from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimestampedModel


class SizeLabelPolicy(TimestampedModel):
    """Global, singleton thresholds used to classify changed lines."""

    SINGLETON_PK = 1

    enabled = models.BooleanField(default=True)
    xs_max = models.PositiveIntegerField(default=10)
    s_max = models.PositiveIntegerField(default=50)
    m_max = models.PositiveIntegerField(default=250)
    l_max = models.PositiveIntegerField(default=1000)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        """Store the singleton in its own explicit table."""

        db_table = "size_label_policy"

    @classmethod
    def load(cls) -> "SizeLabelPolicy":
        """Return the one global policy, creating defaults when necessary."""
        policy, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return policy

    def clean(self) -> None:
        """Require ordered, non-overlapping size bands."""
        super().clean()
        thresholds = [self.xs_max, self.s_max, self.m_max, self.l_max]
        if any(left >= right for left, right in zip(thresholds[:-1], thresholds[1:], strict=True)):
            raise ValidationError("Size thresholds must be strictly increasing: XS < S < M < L.")

    def classify(self, changed_lines: int) -> str:
        """Return the managed label for a changed-line total."""
        for label, maximum in (
            ("size/XS", self.xs_max),
            ("size/S", self.s_max),
            ("size/M", self.m_max),
            ("size/L", self.l_max),
        ):
            if changed_lines <= maximum:
                return label
        return "size/XL"

    def __str__(self) -> str:
        """Return the singleton's dashboard name."""
        return "Global merge-request size policy"


class MergeRequestSizeResult(TimestampedModel):
    """Latest size calculation and remote-label state for one merge request."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    STATUSES = (
        (PENDING, "Pending"),
        (PROCESSING, "Processing"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    )

    merge_request = models.OneToOneField(
        "projects.MergeRequest", on_delete=models.CASCADE, related_name="size_result"
    )
    source_sha = models.CharField(max_length=64, blank=True)
    additions = models.PositiveIntegerField(default=0)
    deletions = models.PositiveIntegerField(default=0)
    changed_lines = models.PositiveIntegerField(default=0)
    label = models.CharField(max_length=32, blank=True)
    policy_revision = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUSES, default=PENDING)
    error_message = models.TextField(blank=True)
    labeled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Configure result storage and reconciliation lookup."""

        db_table = "merge_request_size_results"
        indexes = [models.Index(fields=["status", "policy_revision"])]

    def __str__(self) -> str:
        """Return the merge request and current label state."""
        return f"{self.merge_request}: {self.label or self.get_status_display()}"

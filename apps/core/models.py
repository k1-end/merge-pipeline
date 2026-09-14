"""Base models shared across the domain apps."""

from django.db import models


class TimestampedModel(models.Model):
    """Abstract base model providing created_at / updated_at timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Metadata for timestamped models."""

        abstract = True
        ordering = ["-created_at"]


class IsActiveModel(models.Model):
    """Abstract base model providing a soft enable/disable flag."""

    is_active = models.BooleanField(default=True)

    class Meta:
        """Metadata for is-active models."""

        abstract = True

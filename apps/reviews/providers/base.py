"""Provider contracts and shared HTTP implementation."""

from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.projects.models import MRChange
from apps.reviews.models import AIProvider


@dataclass
class ReviewRequest:
    """Input sent to an AI provider."""
    mr_title: str
    mr_description: str
    changes: list[MRChange]
    system_prompt: str
    review_prompt: str
    workspace_path: str | None = None
    source_branch: str = ""
    target_branch: str = ""


@dataclass
class ReviewResponse:
    """Normalized provider response."""
    summary: str
    findings: list[dict]
    raw_response: Any
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int


class ProviderError(RuntimeError):
    """Raised for provider failures that should fail a review."""


class BaseAIProvider:
    """Base provider implementation."""

    def __init__(self, config: AIProvider):
        """Initialize a provider from persisted configuration."""
        self.config = config

    def estimate_cost(self, request: ReviewRequest) -> float:
        """Estimate cost using configured per-token rates."""
        tokens = len(self._prompt(request)) // 4
        return float(tokens * self.config.input_cost_per_million / 1_000_000)

    def _prompt(self, request: ReviewRequest) -> str:
        """Render title, description, and diffs into a bounded prompt."""
        changes = "\n\n".join(f"FILE: {change.file_path}\n{change.diff}" for change in request.changes)
        changes = changes[: settings.MAX_REVIEW_DIFF_BYTES]
        return request.review_prompt.format(title=request.mr_title, description=request.mr_description, changes=changes)

    def review(self, request: ReviewRequest) -> ReviewResponse:
        """Call the provider and normalize its response."""
        raise NotImplementedError

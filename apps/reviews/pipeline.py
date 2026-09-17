"""Cost-aware review orchestration."""

from time import monotonic

from django.db import transaction
from django.utils import timezone

from apps.projects.models import MergeRequest
from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.reviews.parsers import parse_review_response
from apps.reviews.providers import PROVIDER_MAP
from apps.reviews.providers.base import ReviewRequest

DEFAULT_SYSTEM_PROMPT = "You are a careful senior code reviewer. Return only JSON with summary and findings."
DEFAULT_REVIEW_PROMPT = "Review this change.\nTitle: {title}\nDescription: {description}\nChanges:\n{changes}"


class BudgetExceededError(RuntimeError):
    """Raised before a review would exceed its configured budget."""


class ReviewPipeline:
    """Run one review with provider selection and budget enforcement."""

    def __init__(self, budget_limit_usd: float = 10.0):
        """Initialize the maximum estimated spend for one review."""
        self.budget_limit = budget_limit_usd

    def select_provider(self, mr: MergeRequest) -> AIProvider:
        """Select the project override or active default provider."""
        provider = mr.project.ai_provider or AIProvider.objects.filter(is_default=True, is_active=True).first()
        if provider is None:
            raise ValueError("No active AI provider configured")
        return provider

    @transaction.atomic
    def run_review(self, mr: MergeRequest) -> Review:
        """Execute and persist a complete review."""
        provider_config = self.select_provider(mr)
        provider = PROVIDER_MAP[provider_config.provider_type](provider_config)
        request = ReviewRequest(mr.title, mr.description, list(mr.changes.all()), provider_config.system_prompt or DEFAULT_SYSTEM_PROMPT, provider_config.review_prompt_template or DEFAULT_REVIEW_PROMPT)
        if provider.estimate_cost(request) > self.budget_limit:
            raise BudgetExceededError("Review exceeds configured budget")
        review = Review.objects.create(merge_request=mr, provider=provider_config, status=Review.PROCESSING)
        started = monotonic()
        try:
            response = provider.review(request)
            summary, findings = parse_review_response(response.summary)
            review.summary = summary
            review.findings = findings
            review.raw_response = response.raw_response
            review.input_tokens = response.input_tokens
            review.output_tokens = response.output_tokens
            review.cost_usd = response.cost_usd
            review.duration_ms = response.duration_ms or int((monotonic() - started) * 1000)
            review.status = Review.COMPLETED
            review.completed_at = timezone.now()
            review.save()
            ReviewComment.objects.bulk_create([ReviewComment(review=review, **finding) for finding in findings])
        except Exception as exc:
            review.status = Review.FAILED
            review.error_message = str(exc)
            review.duration_ms = int((monotonic() - started) * 1000)
            review.save(update_fields=["status", "error_message", "duration_ms", "updated_at"])
            raise
        return review

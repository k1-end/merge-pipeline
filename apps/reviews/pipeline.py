"""Cost-aware review orchestration."""

import json
from contextlib import nullcontext
from time import monotonic

from django.db import transaction
from django.utils import timezone

from apps.projects.models import MergeRequest
from apps.reviews.execution_logs import ReviewExecutionLogRecorder
from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.reviews.parsers import parse_review_response
from apps.reviews.providers import PROVIDER_MAP
from apps.reviews.providers.base import ReviewRequest
from apps.reviews.workspaces import ReviewWorkspace

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
        provider = (
            mr.project.ai_provider or AIProvider.objects.filter(is_default=True, is_active=True).first()
        )
        if provider is None:
            raise ValueError("No active AI provider configured")
        return provider

    def run_review(self, mr: MergeRequest) -> Review:
        """Execute and persist a complete review."""
        provider_config = self.select_provider(mr)
        provider = PROVIDER_MAP[provider_config.provider_type](provider_config)
        request = ReviewRequest(
            mr.title,
            mr.description,
            list(mr.changes.all()),
            provider_config.system_prompt or DEFAULT_SYSTEM_PROMPT,
            provider_config.review_prompt_template or DEFAULT_REVIEW_PROMPT,
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
        )
        if provider.estimate_cost(request) > self.budget_limit:
            raise BudgetExceededError("Review exceeds configured budget")
        review = Review.objects.create(merge_request=mr, provider=provider_config, status=Review.PROCESSING)
        log_recorder = ReviewExecutionLogRecorder(review)
        request.log_handler = log_recorder.write
        log_recorder.write(
            "system",
            f"Starting provider {provider_config.provider_type} with model {provider_config.model_name}\n",
        )
        started = monotonic()
        try:
            workspace = (
                ReviewWorkspace(mr.project, mr.source_branch, mr.target_branch)
                if provider_config.provider_type == AIProvider.OPENCODE
                else nullcontext(None)
            )
            with workspace as workspace_path:
                request.workspace_path = workspace_path
                if isinstance(workspace, ReviewWorkspace):
                    request.base_sha = workspace.base_sha
                    request.head_sha = workspace.head_sha
                    request.merge_base_sha = workspace.merge_base_sha
                    review.source_branch = mr.source_branch
                    review.target_branch = mr.target_branch
                    review.base_sha = workspace.base_sha
                    review.head_sha = workspace.head_sha
                    review.merge_base_sha = workspace.merge_base_sha
                response = provider.review(request)
            log_recorder.write("provider", _serialize_provider_response(response.raw_response))
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
            with transaction.atomic():
                review.save()
                ReviewComment.objects.bulk_create(
                    [ReviewComment(review=review, **finding) for finding in findings]
                )
        except Exception as exc:
            log_recorder.write("system", f"Review failed: {type(exc).__name__}: {exc}\n")
            review.status = Review.FAILED
            review.error_message = str(exc)
            review.duration_ms = int((monotonic() - started) * 1000)
            review.save(
                update_fields=[
                    "status",
                    "error_message",
                    "duration_ms",
                    "source_branch",
                    "target_branch",
                    "base_sha",
                    "head_sha",
                    "merge_base_sha",
                    "updated_at",
                ]
            )
            raise
        return review


def _serialize_provider_response(raw_response: object) -> str:
    """Render the complete normalized provider response without a size cap."""
    if isinstance(raw_response, str):
        return raw_response
    return json.dumps(raw_response, ensure_ascii=False, indent=2, default=str)

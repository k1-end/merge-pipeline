"""Cost-aware review orchestration."""

import json
import logging
from contextlib import nullcontext
from time import monotonic

from django.db import transaction
from django.utils import timezone

from apps.projects.models import MergeRequest, MRChange, Project
from apps.reviews.execution_logs import ReviewExecutionLogRecorder
from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.reviews.parsers import parse_review_response
from apps.reviews.providers import PROVIDER_MAP
from apps.reviews.providers.base import ReviewRequest
from apps.reviews.workspaces import ReviewWorkspace
from apps.sources.factory import get_adaptor

DEFAULT_SYSTEM_PROMPT = "You are a careful senior code reviewer. Return only JSON with summary and findings."
DEFAULT_REVIEW_PROMPT = "Review this change.\nTitle: {title}\nDescription: {description}\nChanges:\n{changes}"
logger = logging.getLogger(__name__)


class BudgetExceededError(RuntimeError):
    """Raised before a review would exceed its configured budget."""


class ReviewStateError(RuntimeError):
    """Raised when a queued review can no longer transition to processing."""


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

    def run_review(
        self,
        mr: MergeRequest,
        celery_task_id: str | None = None,
        pending_review: Review | None = None,
    ) -> Review:
        """Execute and persist a complete review."""
        provider_config = pending_review.provider if pending_review is not None else self.select_provider(mr)
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
        if pending_review is None:
            review = Review.objects.create(
                merge_request=mr,
                provider=provider_config,
                status=Review.PROCESSING,
                celery_task_id=celery_task_id,
            )
        else:
            started_at = timezone.now()
            transitioned = Review.objects.filter(pk=pending_review.pk, status=Review.PENDING).update(
                status=Review.PROCESSING, updated_at=started_at
            )
            if not transitioned:
                raise ReviewStateError("Queued review is no longer pending")
            review = pending_review
            review.status = Review.PROCESSING
        started = monotonic()
        log_recorder = None
        try:
            log_recorder = ReviewExecutionLogRecorder(review)
            request.log_handler = log_recorder.write
            log_recorder.write(
                "system",
                f"Starting provider {provider_config.provider_type} with model {provider_config.model_name}\n",
            )
            review.diff_snapshot = get_adaptor(mr.project.source).fetch_review_snapshot(
                mr.project, mr.external_iid
            )
            request.changes = [MRChange(**change) for change in review.diff_snapshot["changes"]]
            request.system_prompt += '\nReturn findings with file (repository-relative path), line (positive integer or null), side ("new" or "old"), type, severity, and message. Use exact diff line numbers where possible. Do not omit findings when their location is unavailable.'
            revision = review.diff_snapshot["revision"]
            request.head_sha = review.head_sha = revision["head_sha"]
            request.base_sha = review.base_sha = revision["base_sha"]
            review.source_branch = mr.source_branch
            review.target_branch = mr.target_branch
            snapshot_saved = Review.objects.filter(pk=review.pk, status=Review.PROCESSING).update(
                diff_snapshot=review.diff_snapshot,
                source_branch=review.source_branch,
                target_branch=review.target_branch,
                base_sha=review.base_sha,
                head_sha=review.head_sha,
                updated_at=timezone.now(),
            )
            if not snapshot_saved:
                raise ReviewStateError("Review is no longer processing")
            if provider.estimate_cost(request) > self.budget_limit:
                raise BudgetExceededError("Current remote diff exceeds configured budget")
            workspace = (
                ReviewWorkspace(
                    mr.project,
                    mr.source_branch,
                    mr.target_branch,
                    base_sha=revision.get("start_sha", revision["base_sha"]),
                    head_sha=revision["head_sha"],
                    merge_base_sha=revision["base_sha"] if "start_sha" in revision else "",
                )
                if provider_config.provider_type == AIProvider.OPENCODE
                else nullcontext(None)
            )
            with workspace as workspace_path:
                request.workspace_path = workspace_path
                if isinstance(workspace, ReviewWorkspace):
                    if workspace.head_sha != revision["head_sha"]:
                        raise ReviewStateError("Review workspace head does not match the pinned snapshot")
                    if workspace.base_sha != revision.get("start_sha", revision["base_sha"]):
                        raise ReviewStateError("Review workspace target does not match the pinned snapshot")
                    if "start_sha" in revision and workspace.merge_base_sha != revision["base_sha"]:
                        raise ReviewStateError(
                            "Review workspace merge base does not match the pinned snapshot"
                        )
                    request.base_sha = workspace.base_sha
                    request.head_sha = workspace.head_sha
                    request.merge_base_sha = workspace.merge_base_sha
                    review.source_branch = mr.source_branch
                    review.target_branch = mr.target_branch
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
            completed_at = timezone.now()
            with transaction.atomic():
                completed = Review.objects.filter(pk=review.pk, status=Review.PROCESSING).update(
                    summary=summary,
                    findings=findings,
                    raw_response=response.raw_response,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=response.cost_usd,
                    duration_ms=review.duration_ms,
                    status=Review.COMPLETED,
                    completed_at=completed_at,
                    source_branch=review.source_branch,
                    target_branch=review.target_branch,
                    base_sha=review.base_sha,
                    head_sha=review.head_sha,
                    merge_base_sha=review.merge_base_sha,
                    diff_snapshot=review.diff_snapshot,
                    updated_at=completed_at,
                )
                if completed:
                    ReviewComment.objects.bulk_create(
                        [ReviewComment(review=review, **finding) for finding in findings]
                    )
                    if (
                        findings
                        and Project.objects.filter(pk=mr.project_id, auto_post_comments_enabled=True).exists()
                    ):
                        transaction.on_commit(lambda: _queue_automatic_comments(review.pk))
            review.refresh_from_db()
        except Exception as exc:
            if log_recorder is not None:
                try:
                    log_recorder.write("system", f"Review failed: {type(exc).__name__}: {exc}\n")
                except Exception:  # noqa: BLE001 - log storage must not block status persistence
                    logger.exception(
                        "Could not persist the final review failure log", extra={"review_id": review.pk}
                    )
            failed_at = timezone.now()
            Review.objects.filter(pk=review.pk, status=Review.PROCESSING).update(
                status=Review.FAILED,
                error_message=str(exc),
                duration_ms=int((monotonic() - started) * 1000),
                completed_at=failed_at,
                source_branch=review.source_branch,
                target_branch=review.target_branch,
                base_sha=review.base_sha,
                head_sha=review.head_sha,
                merge_base_sha=review.merge_base_sha,
                diff_snapshot=review.diff_snapshot,
                updated_at=failed_at,
            )
            review.refresh_from_db()
            raise
        return review


def _queue_automatic_comments(review_id: int) -> None:
    """Keep broker failures separate from successful, potentially paid AI execution."""
    from apps.reviews.tasks import post_review_comments

    try:
        post_review_comments.delay(review_id, automatic=True)
    except Exception:  # noqa: BLE001 - publishing must not cause the AI review to rerun
        logger.exception("Could not queue automatic review comments", extra={"review_id": review_id})


def _serialize_provider_response(raw_response: object) -> str:
    """Render the complete normalized provider response without a size cap."""
    if isinstance(raw_response, str):
        return raw_response
    return json.dumps(raw_response, ensure_ascii=False, indent=2, default=str)

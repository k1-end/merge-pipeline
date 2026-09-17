from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from apps.projects.models import MergeRequest, MRChange, Project
from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.reviews.parsers import ReviewParseError, parse_review_response
from apps.reviews.pipeline import BudgetExceededError, ReviewPipeline
from apps.reviews.services import ReviewActionService
from apps.sources.models import GitSource


@pytest.fixture
def project(db: Any) -> Project:
    """Create a project for review tests."""
    source = GitSource.objects.create(name="reviews-source", source_type="github", base_url="https://api.github.com")
    return Project.objects.create(source=source, external_id="1", name="api", full_name="org/api", web_url="https://github.com/org/api")


@pytest.fixture
def merge_request(project: Project) -> MergeRequest:
    """Create a merge request with one changed file."""
    mr = MergeRequest.objects.create(project=project, external_id="10", external_iid="10", title="Fix", state="opened", author_username="dev", source_branch="fix", target_branch="main", web_url="https://github.com/org/api/pull/10", opened_at=datetime.now(UTC), external_updated_at=datetime.now(UTC))
    MRChange.objects.create(merge_request=mr, file_path="app.py", change_type="modified", diff="@@")
    return mr


def test_parse_review_response_accepts_fenced_json() -> None:
    """Structured output can be returned in a markdown JSON fence."""
    summary, findings = parse_review_response('```json\n{"summary":"Looks good","findings":[{"file":"app.py","line":4,"type":"issue","message":"Fix this"}]}\n```')
    assert summary == "Looks good"
    assert findings[0]["file_path"] == "app.py"


def test_parse_review_response_rejects_invalid_json() -> None:
    """Malformed model output is not persisted as a completed review."""
    with pytest.raises(ReviewParseError):
        parse_review_response("not json")


def test_pipeline_persists_structured_review(project: Project, merge_request: MergeRequest, monkeypatch: Any, settings: Any) -> None:
    """A provider response becomes a review and comment records."""
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    AIProvider.objects.create(name="mock", provider_type="custom", model_name="mock", is_default=True)
    response = Mock(summary='{"summary":"Found one issue","findings":[{"file":"app.py","line":2,"message":"Bad path"}]}', raw_response={"ok": True}, input_tokens=10, output_tokens=5, cost_usd=0.01, duration_ms=4)
    fake_provider = Mock(estimate_cost=Mock(return_value=0.01), review=Mock(return_value=response))
    monkeypatch.setitem(__import__("apps.reviews.pipeline", fromlist=["PROVIDER_MAP"]).PROVIDER_MAP, "custom", lambda config: fake_provider)
    review = ReviewPipeline().run_review(merge_request)
    assert review.status == Review.COMPLETED
    assert ReviewComment.objects.get(review=review).content == "Bad path"


def test_pipeline_enforces_budget(merge_request: MergeRequest) -> None:
    """Expensive requests are rejected before a provider call."""
    AIProvider.objects.create(name="expensive", provider_type="custom", model_name="mock", is_default=True, input_cost_per_million=1000)
    with pytest.raises(BudgetExceededError):
        ReviewPipeline(budget_limit_usd=0).run_review(merge_request)


def test_post_comments_is_idempotent(merge_request: MergeRequest, monkeypatch: Any) -> None:
    """Already-posted comments are not sent again."""
    provider = AIProvider.objects.create(name="actions", provider_type="custom", model_name="mock")
    review = Review.objects.create(merge_request=merge_request, provider=provider, status=Review.COMPLETED)
    pending = ReviewComment.objects.create(review=review, file_path="app.py", line_number=2, comment_type="issue", content="Fix")
    posted = ReviewComment.objects.create(review=review, file_path="old.py", comment_type="issue", content="Done", external_comment_id="100")
    adaptor = Mock(create_comment=Mock(return_value="101"))
    monkeypatch.setattr("apps.reviews.services.get_adaptor", lambda source: adaptor)
    assert ReviewActionService.post_comments(review) == 1
    pending.refresh_from_db()
    posted.refresh_from_db()
    assert pending.external_comment_id == "101"
    assert posted.external_comment_id == "100"
    adaptor.create_comment.assert_called_once()


def test_update_mr_state_updates_local_record(merge_request: MergeRequest, monkeypatch: Any) -> None:
    """Local state follows a successful provider action."""
    adaptor = Mock(update_mr_state=Mock(return_value=True))
    monkeypatch.setattr("apps.reviews.services.get_adaptor", lambda source: adaptor)
    assert ReviewActionService.update_mr_state(merge_request, "merge") is True
    merge_request.refresh_from_db()
    assert merge_request.state == "merged"
    adaptor.update_mr_state.assert_called_once()

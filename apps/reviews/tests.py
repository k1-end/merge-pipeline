import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from cryptography.fernet import Fernet

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.projects.models import MergeRequest, MRChange, Project
from apps.reviews.execution_logs import ReviewExecutionLogRecorder
from apps.reviews.models import AIProvider, Review, ReviewComment, ReviewExecutionLogChunk
from apps.reviews.opencode_output import collect_assistant_text
from apps.reviews.parsers import ReviewParseError, parse_review_response
from apps.reviews.pipeline import BudgetExceededError, ReviewPipeline
from apps.reviews.providers.base import ProviderError, ReviewRequest
from apps.reviews.providers.opencode import OpenCodeProvider
from apps.reviews.repository_cache import (
    RepositoryCache,
    RepositoryCacheConfigurationError,
    RepositoryCacheMissingError,
)
from apps.reviews.services import (
    ReviewActionService,
    ReviewAlreadyRunningError,
    ReviewConfigurationError,
    ReviewQueueService,
)
from apps.reviews.tasks import _raise_or_retry_missing_cache
from apps.reviews.workspaces import ReviewWorkspace
from apps.sources.models import GitSource


def popen_mock(stdout: bytes, stderr: bytes, returncode: int) -> Mock:
    """Build a Popen mock that writes bytes to the supplied spool files."""
    process = Mock()
    process.wait.return_value = returncode

    def create(*args: Any, **kwargs: Any) -> Mock:
        kwargs["stdout"].write(stdout)
        kwargs["stderr"].write(stderr)
        return process

    return Mock(side_effect=create)


def run_git(cwd: Path, *arguments: str) -> None:
    """Run trusted Git setup commands for integration fixtures."""
    executable = shutil.which("git")
    assert executable is not None
    subprocess.run(  # noqa: S603 - test arguments are static
        [executable, *arguments], cwd=cwd, check=True, capture_output=True
    )


def read_git(cwd: Path, *arguments: str) -> str:
    """Return output from trusted read-only Git fixture commands."""
    executable = shutil.which("git")
    assert executable is not None
    return subprocess.run(  # noqa: S603 - test arguments are static
        [executable, *arguments], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def project(db: Any) -> Project:
    """Create a project for review tests."""
    source = GitSource.objects.create(
        name="reviews-source", source_type="github", base_url="https://api.github.com"
    )
    return Project.objects.create(
        source=source, external_id="1", name="api", full_name="org/api", web_url="https://github.com/org/api"
    )


@pytest.fixture
def merge_request(project: Project) -> MergeRequest:
    """Create a merge request with one changed file."""
    mr = MergeRequest.objects.create(
        project=project,
        external_id="10",
        external_iid="10",
        title="Fix",
        state="opened",
        author_username="dev",
        source_branch="fix",
        target_branch="main",
        web_url="https://github.com/org/api/pull/10",
        opened_at=datetime.now(UTC),
        external_updated_at=datetime.now(UTC),
    )
    MRChange.objects.create(merge_request=mr, file_path="app.py", change_type="modified", diff="@@")
    return mr


def test_parse_review_response_accepts_fenced_json() -> None:
    """Structured output can be returned in a markdown JSON fence."""
    summary, findings = parse_review_response(
        '```json\n{"summary":"Looks good","findings":[{"file":"app.py","line":4,"type":"issue","message":"Fix this"}]}\n```'
    )
    assert summary == "Looks good"
    assert findings[0]["file_path"] == "app.py"


def test_parse_review_response_rejects_invalid_json() -> None:
    """Malformed model output is not persisted as a completed review."""
    with pytest.raises(ReviewParseError):
        parse_review_response("not json")


def test_collect_assistant_text_reads_opencode_events() -> None:
    """Only text parts from OpenCode events are sent to the review parser."""
    output = "\n".join(
        [
            '{"type":"step-start","properties":{"model":"opencode/test"}}',
            '{"type":"message.part.updated","properties":{"part":{"type":"text","text":"{\\"summary\\":\\"ok\\",\\"findings\\":[]}"}}}',
        ]
    )
    content, events = collect_assistant_text(output)
    assert content == '{"summary":"ok","findings":[]}'
    assert len(events) == 2


def test_opencode_provider_invokes_read_only_agent(monkeypatch: Any, settings: Any) -> None:
    """The executor passes the workspace and agent to the non-interactive CLI."""
    settings.OPENCODE_BINARY = "opencode"
    settings.OPENCODE_AGENT = "review"
    settings.OPENCODE_TIMEOUT_SECONDS = 30
    settings.OPENCODE_MAX_OUTPUT_BYTES = 100_000
    popen = popen_mock(
        b'{"type":"text","text":"{\\"summary\\":\\"ok\\",\\"findings\\":[]}"}\n',
        b"",
        0,
    )
    monkeypatch.setattr("apps.reviews.providers.opencode.subprocess.Popen", popen)
    config = AIProvider(name="opencode", provider_type=AIProvider.OPENCODE, model_name="opencode/test")
    response = OpenCodeProvider(config).review(
        ReviewRequest(
            "Fix",
            "",
            [],
            "Review",
            "Review {title}",
            workspace_path="review-workspace",
            source_branch="fix",
            target_branch="main",
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
        )
    )
    assert response.summary == '{"summary":"ok","findings":[]}'
    command = popen.call_args.args[0]
    assert command[:8] == [
        "opencode",
        "run",
        "--agent",
        "review",
        "--model",
        "opencode/test",
        "--format",
        "json",
    ]
    assert f"git diff --find-renames {'a' * 40}...{'b' * 40}" in command[-1]


def test_opencode_provider_emits_complete_untruncated_output(
    monkeypatch: Any, settings: Any
) -> None:
    """OpenCode stdout and stderr are emitted in full even above the legacy limit."""
    settings.OPENCODE_BINARY = "opencode"
    settings.OPENCODE_AGENT = "review"
    settings.OPENCODE_TIMEOUT_SECONDS = 30
    settings.OPENCODE_MAX_OUTPUT_BYTES = 8
    stdout = '{"type":"text","text":"{\\"summary\\":\\"ok\\",\\"findings\\":[]}"}\n'
    stderr = "diagnostic-" * 1000
    monkeypatch.setattr(
        "apps.reviews.providers.opencode.subprocess.Popen",
        popen_mock(stdout.encode(), stderr.encode(), 0),
    )
    captured: list[tuple[str, str | bytes]] = []
    request = ReviewRequest(
        "Fix",
        "",
        [],
        "Review",
        "Review {title}",
        workspace_path="review-workspace",
        log_handler=lambda stream, content: captured.append((stream, content)),
    )

    OpenCodeProvider(
        AIProvider(name="opencode", provider_type=AIProvider.OPENCODE, model_name="opencode/test")
    ).review(request)

    assert (ReviewExecutionLogChunk.STDOUT, stdout.encode()) in captured
    assert (ReviewExecutionLogChunk.STDERR, stderr.encode()) in captured


def test_opencode_provider_emits_output_before_nonzero_exit(
    monkeypatch: Any, settings: Any
) -> None:
    """Diagnostics from a failed process are retained before the provider raises."""
    settings.OPENCODE_BINARY = "opencode"
    settings.OPENCODE_AGENT = "review"
    settings.OPENCODE_TIMEOUT_SECONDS = 30
    monkeypatch.setattr(
        "apps.reviews.providers.opencode.subprocess.Popen",
        popen_mock(b"partial output", b"fatal detail", 9),
    )
    captured: list[tuple[str, str | bytes]] = []
    request = ReviewRequest(
        "Fix",
        "",
        [],
        "Review",
        "Review {title}",
        workspace_path="review-workspace",
        log_handler=lambda stream, content: captured.append((stream, content)),
    )

    with pytest.raises(ProviderError, match="exit code 9"):
        OpenCodeProvider(
            AIProvider(name="opencode", provider_type=AIProvider.OPENCODE, model_name="opencode/test")
        ).review(request)

    assert (ReviewExecutionLogChunk.STDOUT, b"partial output") in captured
    assert (ReviewExecutionLogChunk.STDERR, b"fatal detail") in captured


def test_manual_review_queue_rejects_active_duplicate(merge_request: MergeRequest) -> None:
    """A second dashboard request cannot create concurrent reviews."""
    merge_request.project.clone_url = "https://github.com/org/api.git"
    merge_request.project.save(update_fields=["clone_url", "updated_at"])
    provider = AIProvider.objects.create(
        name="default", provider_type=AIProvider.CUSTOM, model_name="mock", is_default=True
    )
    Review.objects.create(merge_request=merge_request, provider=provider, status=Review.PROCESSING)
    with pytest.raises(ReviewAlreadyRunningError):
        ReviewQueueService.queue_manual_review(merge_request)


def test_manual_review_queue_rejects_missing_repository_cache(
    merge_request: MergeRequest, settings: Any, tmp_path: Path
) -> None:
    """Manual reviews return an actionable error before queueing without a cache."""
    settings.REPOSITORY_CACHE_ROOT = str(tmp_path / "cache")
    merge_request.project.clone_url = "https://github.com/org/api.git"
    merge_request.project.save(update_fields=["clone_url", "updated_at"])
    AIProvider.objects.create(
        name="default", provider_type=AIProvider.OPENCODE, model_name="opencode/mock", is_default=True
    )

    with pytest.raises(ReviewConfigurationError, match="Clone it from the Projects page"):
        ReviewQueueService.queue_manual_review(merge_request)


def test_review_workspace_uses_persistent_cache(project: Project, settings: Any, tmp_path: Path) -> None:
    """A cached repository creates and removes a worktree without recloning."""
    settings.REPOSITORY_CACHE_ROOT = str(tmp_path / "cache")
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    origin = tmp_path / "origin"
    origin.mkdir()
    run_git(origin, "init", "-b", "main")
    run_git(origin, "config", "user.email", "review@example.com")
    run_git(origin, "config", "user.name", "Review Test")
    (origin / "app.py").write_text("main\n", encoding="utf-8")
    run_git(origin, "add", "app.py")
    run_git(origin, "commit", "-m", "main")
    run_git(origin, "checkout", "-b", "fix")
    (origin / "app.py").write_text("fix\n", encoding="utf-8")
    run_git(origin, "commit", "-am", "fix")

    project.clone_url = origin.as_uri()
    project.save(update_fields=["clone_url", "updated_at"])
    GitSourceCredential.objects.create(
        source=project.source,
        access_token_encrypted=CredentialEncryption().encrypt("test-token"),
    )
    cache = RepositoryCache(project)
    cache.clone()

    assert cache.exists()
    main_sha = read_git(origin, "rev-parse", "main")
    fix_sha = read_git(origin, "rev-parse", "fix")

    review_workspace = ReviewWorkspace(project, "fix", "main")
    with review_workspace as workspace:
        workspace_path = Path(workspace)
        assert (workspace_path / "app.py").read_text(encoding="utf-8") == "fix\n"
        assert workspace_path.is_dir()
        assert review_workspace.base_sha == main_sha
        assert review_workspace.head_sha == fix_sha
        assert review_workspace.merge_base_sha == main_sha
        assert read_git(workspace_path, "diff", "--name-only", f"{main_sha}...{fix_sha}") == "app.py"
    assert not workspace_path.exists()
    assert cache.exists()

    project.clone_url = (tmp_path / "different-origin").as_uri()
    assert not cache.exists()


def test_repository_cache_rejects_credentials_in_clone_url(
    project: Project, settings: Any, tmp_path: Path
) -> None:
    """Embedded URL credentials are never persisted in bare repository configuration."""
    settings.REPOSITORY_CACHE_ROOT = str(tmp_path / "cache")
    project.clone_url = "https://secret@example.com/org/api.git"

    with pytest.raises(RepositoryCacheConfigurationError, match="must not contain credentials"):
        RepositoryCache(project).clone()


def test_missing_cache_retries_while_clone_is_running(project: Project) -> None:
    """An automatic review waits for an explicitly queued clone instead of failing permanently."""
    project.cache_status = Project.CACHE_CLONING
    project.save(update_fields=["cache_status", "updated_at"])
    task = Mock()
    task.retry.side_effect = Retry()
    error = RepositoryCacheMissingError("Repository cache is missing")

    with pytest.raises(Retry):
        _raise_or_retry_missing_cache(task, project, error)

    task.retry.assert_called_once_with(exc=error, countdown=30)


def test_pipeline_persists_structured_review(
    project: Project, merge_request: MergeRequest, monkeypatch: Any, settings: Any
) -> None:
    """A provider response becomes a review and comment records."""
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    AIProvider.objects.create(name="mock", provider_type="custom", model_name="mock", is_default=True)
    response = Mock(
        summary='{"summary":"Found one issue","findings":[{"file":"app.py","line":2,"message":"Bad path"}]}',
        raw_response={"ok": True},
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.01,
        duration_ms=4,
    )
    fake_provider = Mock(estimate_cost=Mock(return_value=0.01), review=Mock(return_value=response))
    monkeypatch.setitem(
        __import__("apps.reviews.pipeline", fromlist=["PROVIDER_MAP"]).PROVIDER_MAP,
        "custom",
        lambda config: fake_provider,
    )
    review = ReviewPipeline().run_review(merge_request)
    assert review.status == Review.COMPLETED
    assert ReviewComment.objects.get(review=review).content == "Bad path"
    provider_log = b"".join(
        review.execution_log_chunks.filter(
            stream=ReviewExecutionLogChunk.PROVIDER
        ).values_list("content", flat=True)
    )
    assert b'"ok": true' in provider_log


def test_pipeline_persists_all_log_chunks_for_failed_review(
    merge_request: MergeRequest, monkeypatch: Any
) -> None:
    """A provider failure keeps the failed review and every emitted log chunk."""
    AIProvider.objects.create(
        name="failing", provider_type=AIProvider.CUSTOM, model_name="mock", is_default=True
    )
    first = "a" * (ReviewExecutionLogChunk.CHUNK_SIZE + 17)
    second = "failure diagnostics"

    class FailingProvider:
        def __init__(self, config: AIProvider) -> None:
            self.config = config

        def estimate_cost(self, request: ReviewRequest) -> float:
            return 0

        def review(self, request: ReviewRequest) -> None:
            request.write_log(ReviewExecutionLogChunk.STDOUT, first)
            request.write_log(ReviewExecutionLogChunk.STDERR, second)
            raise ProviderError("provider failed")

    monkeypatch.setitem(
        __import__("apps.reviews.pipeline", fromlist=["PROVIDER_MAP"]).PROVIDER_MAP,
        AIProvider.CUSTOM,
        FailingProvider,
    )

    with pytest.raises(ProviderError, match="provider failed"):
        ReviewPipeline().run_review(merge_request)

    review = Review.objects.get(merge_request=merge_request)
    assert review.status == Review.FAILED
    chunks = list(review.execution_log_chunks.order_by("sequence"))
    assert b"".join(
        chunk.content for chunk in chunks if chunk.stream == ReviewExecutionLogChunk.STDOUT
    ) == first.encode()
    assert b"".join(
        chunk.content for chunk in chunks if chunk.stream == ReviewExecutionLogChunk.STDERR
    ) == second.encode()
    assert len([chunk for chunk in chunks if chunk.stream == ReviewExecutionLogChunk.STDOUT]) == 2


def test_execution_log_recorder_preserves_arbitrary_bytes(merge_request: MergeRequest) -> None:
    """Provider bytes round-trip exactly without replacement decoding or truncation."""
    provider = AIProvider.objects.create(name="binary", provider_type=AIProvider.CUSTOM, model_name="mock")
    review = Review.objects.create(merge_request=merge_request, provider=provider)
    output = b"valid utf-8\n\xff\xfe\x00binary"

    ReviewExecutionLogRecorder(review).write(ReviewExecutionLogChunk.STDOUT, output)

    persisted = b"".join(review.execution_log_chunks.values_list("content", flat=True))
    assert persisted == output


def test_pipeline_enforces_budget(merge_request: MergeRequest) -> None:
    """Expensive requests are rejected before a provider call."""
    AIProvider.objects.create(
        name="expensive",
        provider_type="custom",
        model_name="mock",
        is_default=True,
        input_cost_per_million=1000,
    )
    with pytest.raises(BudgetExceededError):
        ReviewPipeline(budget_limit_usd=0).run_review(merge_request)


def test_post_comments_is_idempotent(merge_request: MergeRequest, monkeypatch: Any) -> None:
    """Already-posted comments are not sent again."""
    provider = AIProvider.objects.create(name="actions", provider_type="custom", model_name="mock")
    review = Review.objects.create(merge_request=merge_request, provider=provider, status=Review.COMPLETED)
    pending = ReviewComment.objects.create(
        review=review, file_path="app.py", line_number=2, comment_type="issue", content="Fix"
    )
    posted = ReviewComment.objects.create(
        review=review, file_path="old.py", comment_type="issue", content="Done", external_comment_id="100"
    )
    adaptor = Mock(find_comment_by_marker=Mock(return_value=None), create_comment=Mock(return_value="101"))
    monkeypatch.setattr("apps.reviews.services.get_adaptor", lambda source: adaptor)
    assert ReviewActionService.post_comments(review) == 1
    pending.refresh_from_db()
    posted.refresh_from_db()
    assert pending.external_comment_id == "101"
    assert posted.external_comment_id == "100"
    adaptor.create_comment.assert_called_once()


def test_post_comments_recovers_remote_comment_created_before_local_save(
    merge_request: MergeRequest, monkeypatch: Any
) -> None:
    """A retry adopts a remote comment found by its stable marker instead of duplicating it."""
    provider = AIProvider.objects.create(name="retry-actions", provider_type="custom", model_name="mock")
    review = Review.objects.create(merge_request=merge_request, provider=provider, status=Review.COMPLETED)
    comment = ReviewComment.objects.create(
        review=review, file_path="app.py", line_number=2, comment_type="issue", content="Fix"
    )
    adaptor = Mock(find_comment_by_marker=Mock(return_value="101"), create_comment=Mock())
    monkeypatch.setattr("apps.reviews.services.get_adaptor", lambda source: adaptor)

    assert ReviewActionService.post_comments(review) == 0

    comment.refresh_from_db()
    assert comment.external_comment_id == "101"
    adaptor.create_comment.assert_not_called()


def test_update_mr_state_updates_local_record(merge_request: MergeRequest, monkeypatch: Any) -> None:
    """Local state follows a successful provider action."""
    adaptor = Mock(update_mr_state=Mock(return_value=True))
    monkeypatch.setattr("apps.reviews.services.get_adaptor", lambda source: adaptor)
    assert ReviewActionService.update_mr_state(merge_request, "merge") is True
    merge_request.refresh_from_db()
    assert merge_request.state == "merged"
    adaptor.update_mr_state.assert_called_once()

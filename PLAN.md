# Merge Pipeline — Django Project Plan

A Django application to manage merge requests (MRs) from multiple GitLab instances (including local/self-hosted) and GitHub, with AI-powered code reviews, webhook + polling sync, and Django Admin configuration.

## Table of Contents

1. [Project Overview](#project-overview)
2. [Project Structure](#project-structure)
3. [Database Schema](#database-schema)
4. [Adaptor Architecture](#adaptor-architecture)
5. [AI Review Pipeline](#ai-review-pipeline)
6. [Celery + Webhook Architecture](#celery--webhook-architecture)
7. [Security & Credential Management](#security--credential-management)
8. [Django Admin Configuration](#django-admin-configuration)
9. [Implementation Phases](#implementation-phases)
10. [Key Technical Decisions](#key-technical-decisions)
11. [Environment Variables](#environment-variables)
12. [Verification Checklist](#verification-checklist)
13. [Open Questions](#open-questions)

---

## Project Overview

### Functional Requirements

- Manage merge requests from multiple git platforms (GitLab with local instances, GitHub)
- Multiple adaptors to fetch MR data periodically
- AI reviews MRs and creates/resolves comments
- Manage multiple projects
- UI to configure sources and credentials
- MySQL as data source for MR data and AI reviews
- Close or open/merge MRs

### Technical Decisions (Confirmed with User)

| Area | Decision |
|------|----------|
| AI Provider | Multiple providers or agents (Claude, OpenAI, local LLMs, Codex, Claude Code, OpenCode) |
| Scheduling | Celery Beat |
| UI | Django Admin only |
| GitLab Scale | Many dynamic instances |
| Real-time | Webhooks + polling |

---

## Project Structure

Follows the [django-patterns](https://github.com/ECC/django-patterns) split-settings + app-folder layout.

```
merge_pipeline/
├── config/
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── development.py
│   │   ├── production.py
│   │   └── test.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── apps/
│   ├── __init__.py
│   ├── core/                    # Shared utilities, base models, permissions
│   ├── sources/                 # GitLab, GitHub adaptors, webhook handling
│   ├── reviews/                 # AI review pipeline, multi-provider support
│   ├── projects/                # Project management, MR models
│   ├── credentials/             # Encrypted credential storage
│   ├── sync/                    # Celery tasks, periodic sync, webhook processors
│   └── admin_extensions/        # Custom Django Admin views
├── manage.py
├── requirements/
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
├── docker/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── docker-compose.prod.yml
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Database Schema

Engine: **MySQL (InnoDB)**, `utf8mb4`/`utf8mb4_unicode_ci`. Follows [mysql-patterns](https://github.com/ECC/mysql-patterns): explicit `BIGINT` PKs on high-volume tables, `DECIMAL` for money, composite indexes matching predicates, JSON columns only for payload/extension data.

### Core Models

#### `apps/sources/models.py` — GitSource

```python
class GitSource(models.Model):
    """A configured git source (GitLab local/cloud, GitHub)."""
    SOURCE_TYPES = [
        ('gitlab', 'GitLab'),
        ('github', 'GitHub'),
    ]

    name = models.CharField(max_length=100, unique=True)
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPES)
    base_url = models.URLField(help_text="API base URL (e.g., https://gitlab.com/api/v4)")
    is_active = models.BooleanField(default=True)
    sync_enabled = models.BooleanField(default=True)
    webhook_secret = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'git_sources'
        indexes = [
            models.Index(fields=['source_type', 'is_active']),
        ]
```

#### `apps/sources/models.py` — GitSourceCredential

```python
class GitSourceCredential(models.Model):
    """Encrypted credentials per source."""
    source = models.OneToOneField(GitSource, on_delete=models.CASCADE, related_name='credential')
    access_token_encrypted = models.BinaryField()
    app_id = models.CharField(max_length=100, blank=True)        # GitHub Apps
    private_key_encrypted = models.BinaryField(blank=True)       # GitHub Apps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'git_source_credentials'
```

#### `apps/projects/models.py` — Project

```python
class Project(models.Model):
    """A git project/repository being monitored."""
    source = models.ForeignKey(GitSource, on_delete=models.CASCADE, related_name='projects')
    external_id = models.CharField(max_length=100)          # GitLab/GitHub project ID
    name = models.CharField(max_length=255)
    full_name = models.CharField(max_length=500)            # namespace/project
    web_url = models.URLField()
    default_branch = models.CharField(max_length=100, default='main')
    is_active = models.BooleanField(default=True)
    auto_review_enabled = models.BooleanField(default=False)
    review_on_draft = models.BooleanField(default=False)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'projects'
        unique_together = [['source', 'external_id']]
        indexes = [
            models.Index(fields=['source', 'is_active']),
            models.Index(fields=['-last_synced_at']),
        ]
```

#### `apps/projects/models.py` — MergeRequest

```python
class MergeRequest(models.Model):
    """Merge Request / Pull Request."""
    STATE_CHOICES = [
        ('opened', 'Opened'),
        ('closed', 'Closed'),
        ('merged', 'Merged'),
        ('draft', 'Draft'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='merge_requests')
    external_id = models.CharField(max_length=100)       # MR/PR number
    external_iid = models.CharField(max_length=100)      # GitLab IID
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    state = models.CharField(max_length=20, choices=STATE_CHOICES)
    author_username = models.CharField(max_length=255)
    author_external_id = models.CharField(max_length=100)
    source_branch = models.CharField(max_length=255)
    target_branch = models.CharField(max_length=255)
    web_url = models.URLField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    merged_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(auto_now=True)
    external_updated_at = models.DateTimeField()            # webhook dedup

    class Meta:
        db_table = 'merge_requests'
        unique_together = [['project', 'external_iid']]
        indexes = [
            models.Index(fields=['project', 'state']),
            models.Index(fields=['-updated_at']),
            models.Index(fields=['author_username']),
        ]
```

#### `apps/projects/models.py` — MRChange

```python
class MRChange(models.Model):
    """Track file changes in MR for diff review."""
    merge_request = models.ForeignKey(MergeRequest, on_delete=models.CASCADE, related_name='changes')
    file_path = models.CharField(max_length=1000)
    old_path = models.CharField(max_length=1000, blank=True)
    change_type = models.CharField(max_length=20, choices=[
        ('added', 'Added'), ('modified', 'Modified'),
        ('deleted', 'Deleted'), ('renamed', 'Renamed'),
    ])
    diff = models.TextField(blank=True)                     # Unified diff
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'mr_changes'
        indexes = [models.Index(fields=['merge_request'])]
```

#### `apps/reviews/models.py` — AIProvider

```python
class AIProvider(models.Model):
    """Configured AI provider for reviews."""
    PROVIDER_TYPES = [
        ('anthropic', 'Anthropic (Claude)'),
        ('openai', 'OpenAI'),
        ('ollama', 'Ollama (Local)'),
        ('vllm', 'vLLM (Local)'),
        ('custom', 'Custom HTTP API'),
    ]

    name = models.CharField(max_length=100, unique=True)
    provider_type = models.CharField(max_length=20, choices=PROVIDER_TYPES)
    api_base_url = models.URLField(blank=True)              # local/custom
    model_name = models.CharField(max_length=100)           # claude-3-5-sonnet, gpt-4o, llama3...
    api_key_encrypted = models.BinaryField(blank=True)
    max_tokens_per_request = models.PositiveIntegerField(default=8000)
    temperature = models.FloatField(default=0.1)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)
    system_prompt = models.TextField(blank=True)
    review_prompt_template = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'ai_providers'
```

#### `apps/reviews/models.py` — Review

```python
class Review(models.Model):
    """AI review of a merge request."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    merge_request = models.ForeignKey(MergeRequest, on_delete=models.CASCADE, related_name='reviews')
    provider = models.ForeignKey(AIProvider, on_delete=models.PROTECT, related_name='reviews')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    summary = models.TextField(blank=True)
    findings = models.JSONField(default=list)               # Structured findings
    raw_response = models.JSONField(null=True, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    duration_ms = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'reviews'
        indexes = [
            models.Index(fields=['merge_request', '-created_at']),
            models.Index(fields=['provider', 'status']),
        ]
```

#### `apps/reviews/models.py` — ReviewComment

```python
class ReviewComment(models.Model):
    """Individual review comment on a file/line."""
    COMMENT_TYPES = [
        ('issue', 'Issue'),
        ('suggestion', 'Suggestion'),
        ('praise', 'Praise'),
        ('question', 'Question'),
    ]

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='comments')
    file_path = models.CharField(max_length=1000)
    line_number = models.PositiveIntegerField(null=True, blank=True)   # None = file-level
    comment_type = models.CharField(max_length=20, choices=COMMENT_TYPES)
    content = models.TextField()
    external_comment_id = models.CharField(max_length=100, blank=True) # platform comment ID
    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'review_comments'
        indexes = [
            models.Index(fields=['review']),
            models.Index(fields=['external_comment_id']),
        ]
```

#### `apps/sync/models.py` — SyncLog

```python
class SyncLog(models.Model):
    """Audit log for sync operations."""
    OPERATION_TYPES = [
        ('full_sync', 'Full Sync'),
        ('incremental_sync', 'Incremental Sync'),
        ('webhook_process', 'Webhook Processing'),
        ('mr_state_change', 'MR State Change'),
    ]
    STATUS_CHOICES = [
        ('started', 'Started'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('partial', 'Partial'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE,
                                related_name='sync_logs', null=True, blank=True)
    source = models.ForeignKey(GitSource, on_delete=models.CASCADE, related_name='sync_logs')
    operation = models.CharField(max_length=30, choices=OPERATION_TYPES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='started')
    mr_count = models.PositiveIntegerField(default=0)
    new_mrs = models.PositiveIntegerField(default=0)
    updated_mrs = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    error_details = models.JSONField(default=list)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'sync_logs'
        indexes = [
            models.Index(fields=['source', '-started_at']),
            models.Index(fields=['project', '-started_at']),
        ]
```

#### `apps/sync/models.py` — WebhookEvent

```python
class WebhookEvent(models.Model):
    """Store webhook events for processing and debugging."""
    source = models.ForeignKey(GitSource, on_delete=models.CASCADE, related_name='webhook_events')
    event_type = models.CharField(max_length=100)           # merge_request, push, etc.
    event_action = models.CharField(max_length=50)          # opened, updated, closed
    payload = models.JSONField()
    headers = models.JSONField(default=dict)
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'webhook_events'
        indexes = [
            models.Index(fields=['source', 'processed', '-received_at']),
            models.Index(fields=['event_type', '-received_at']),
        ]
```

---

## Adaptor Architecture

Follows the [api-connector-builder](https://github.com/ECC/api-connector-builder) pattern: repo-native connector layers, dynamic registry, explicit auth/error/pagination conventions.

```
apps/sources/
├── __init__.py
├── base.py                    # Abstract base adaptor
├── gitlab/
│   ├── __init__.py
│   ├── client.py              # GitLab API client with pagination
│   ├── models.py              # Pydantic models for GitLab API responses
│   ├── adaptor.py             # GitLabAdaptor implementing base interface
│   ├── webhook.py             # Webhook signature verification
│   └── exceptions.py
├── github/
│   ├── __init__.py
│   ├── client.py              # GitHub API client (REST + GraphQL)
│   ├── models.py
│   ├── adaptor.py             # GitHubAdaptor
│   ├── webhook.py             # Webhook signature verification
│   └── exceptions.py
├── registry.py                # Dynamic adaptor registry
├── factory.py                 # Adaptor factory for dynamic instantiation
└── exceptions.py
```

### Base Adaptor Interface

```python
# apps/sources/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from apps.projects.models import Project, MergeRequest, MRChange

@dataclass
class SyncResult:
    new_mrs: list[MergeRequest]
    updated_mrs: list[MergeRequest]
    errors: list[str]

class BaseGitAdaptor(ABC):
    """Abstract base for GitLab/GitHub adaptors."""

    def __init__(self, source: GitSource, credential: GitSourceCredential):
        self.source = source
        self.credential = credential
        self.client = self._create_client()

    @abstractmethod
    def _create_client(self):
        pass

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify credentials work."""
        pass

    @abstractmethod
    def fetch_projects(self) -> list[dict]:
        """Return list of accessible projects."""
        pass

    @abstractmethod
    def fetch_merge_requests(self, project: Project, state: str = 'opened',
                             updated_after: Optional[datetime] = None) -> SyncResult:
        """Fetch MRs for a project."""
        pass

    @abstractmethod
    def fetch_mr_changes(self, project: Project, mr_iid: str) -> list[MRChange]:
        """Fetch file changes for an MR."""
        pass

    @abstractmethod
    def create_comment(self, project: Project, mr_iid: str,
                       file_path: str, line: int, content: str) -> str:
        """Post review comment, return external comment ID."""
        pass

    @abstractmethod
    def update_mr_state(self, project: Project, mr_iid: str,
                        action: str) -> bool:   # action: 'close', 'reopen', 'merge'
        """Change MR state."""
        pass

    @abstractmethod
    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature."""
        pass

    @abstractmethod
    def parse_webhook(self, payload: dict) -> dict:
        """Parse webhook to extract MR info."""
        pass
```

### Dynamic Registry Pattern

```python
# apps/sources/registry.py
from typing import Type
from .base import BaseGitAdaptor

class AdaptorRegistry:
    _adaptors: dict[str, Type[BaseGitAdaptor]] = {}

    @classmethod
    def register(cls, source_type: str, adaptor_class: Type[BaseGitAdaptor]):
        cls._adaptors[source_type] = adaptor_class

    @classmethod
    def get(cls, source_type: str) -> Type[BaseGitAdaptor]:
        if source_type not in cls._adaptors:
            raise ValueError(f"No adaptor registered for {source_type}")
        return cls._adaptors[source_type]

    @classmethod
    def get_for_source(cls, source: GitSource) -> BaseGitAdaptor:
        adaptor_class = cls.get(source.source_type)
        return adaptor_class(source, source.credential)

# Auto-registration at app ready
from .gitlab.adaptor import GitLabAdaptor
from .github.adaptor import GitHubAdaptor

AdaptorRegistry.register('gitlab', GitLabAdaptor)
AdaptorRegistry.register('github', GitHubAdaptor)
```

---

## AI Review Pipeline

Multi-provider strategy pattern with a cost-control layer inspired by [cost-aware-llm-pipeline](https://github.com/ECC/cost-aware-llm-pipeline).

```
apps/reviews/
├── __init__.py
├── providers/
│   ├── __init__.py
│   ├── base.py              # Abstract AI provider
│   ├── anthropic.py         # Anthropic implementation
│   ├── openai.py            # OpenAI implementation
│   ├── ollama.py            # Ollama local implementation
│   ├── vllm.py              # vLLM implementation
│   └── custom.py            # Custom HTTP API
├── pipeline.py              # Main review orchestration
├── prompts/
│   ├── __init__.py
│   ├── templates.py         # Jinja2 prompt templates
│   └── system_prompts.py
├── parsers/
│   ├── __init__.py
│   ├── base.py
│   └── structured.py        # Parse AI response to structured findings
├── tasks.py                 # Celery tasks for review
└── services.py              # ReviewService for business logic
```

### Provider Interface

```python
# apps/reviews/providers/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ReviewRequest:
    mr_title: str
    mr_description: str
    changes: list[MRChange]            # File diffs
    project_context: dict              # Language, framework, etc.
    system_prompt: str
    review_prompt: str

@dataclass
class ReviewResponse:
    summary: str
    findings: list[dict]               # {file, line, type, severity, message, suggestion}
    raw_response: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    duration_ms: int

class BaseAIProvider(ABC):
    def __init__(self, provider_config: AIProvider):
        self.config = provider_config
        self.client = self._create_client()

    @abstractmethod
    def _create_client(self):
        pass

    @abstractmethod
    def review(self, request: ReviewRequest) -> ReviewResponse:
        pass

    @abstractmethod
    def estimate_cost(self, request: ReviewRequest) -> float:
        """Estimate cost before calling."""
        pass

    def get_model_name(self) -> str:
        return self.config.model_name
```

### Cost-Aware Pipeline

```python
# apps/reviews/pipeline.py
from dataclasses import dataclass
from apps.reviews.providers import (
    AnthropicProvider, OpenAIProvider, OllamaProvider, VLLMProvider, CustomProvider,
)

PROVIDER_MAP = {
    'anthropic': AnthropicProvider,
    'openai': OpenAIProvider,
    'ollama': OllamaProvider,
    'vllm': VLLMProvider,
    'custom': CustomProvider,
}

class ReviewPipeline:
    def __init__(self, budget_limit_usd: float = 10.0):
        self.budget_limit = budget_limit_usd
        self.cost_tracker = CostTracker(budget_limit=budget_limit_usd)

    def select_provider(self, mr: MergeRequest, project: Project) -> AIProvider:
        """Project override → default provider."""
        if project.ai_provider:
            return project.ai_provider
        return AIProvider.objects.filter(is_default=True, is_active=True).first()

    def run_review(self, mr: MergeRequest) -> Review:
        provider_config = self.select_provider(mr, mr.project)
        provider_class = PROVIDER_MAP[provider_config.provider_type]
        provider = provider_class(provider_config)

        request = ReviewRequest(
            mr_title=mr.title,
            mr_description=mr.description,
            changes=list(mr.changes.all()),
            project_context=self._get_project_context(mr.project),
            system_prompt=provider_config.system_prompt or DEFAULT_SYSTEM_PROMPT,
            review_prompt=provider_config.review_prompt_template or DEFAULT_REVIEW_PROMPT,
        )

        # Budget guard before calling
        estimated_cost = provider.estimate_cost(request)
        if self.cost_tracker.total_cost + estimated_cost > self.budget_limit:
            raise BudgetExceededError(...)

        # Retry only on transient errors, exponential backoff
        response = self._call_with_retry(provider, request)

        # Track cost (immutable tracker)
        self.cost_tracker.add(CostRecord(...))

        return self._save_review(mr, provider_config, response)
```

---

## Celery + Webhook Architecture

```
apps/sync/
├── __init__.py
├── tasks.py                 # Celery tasks
├── periodic.py              # Celery Beat schedule
├── webhook_processor.py     # Process incoming webhooks
├── services.py              # SyncService for business logic
└── signals.py               # Django signals for webhook triggers
```

### Celery Configuration

```python
# config/celery.py
from celery import Celery
from celery.schedules import crontab

app = Celery('merge_pipeline')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'sync-all-active-projects': {
        'task': 'apps.sync.tasks.sync_all_projects',
        'schedule': crontab(minute='*/15'),          # every 15 minutes
    },
    'sync-stale-projects': {
        'task': 'apps.sync.tasks.sync_stale_projects',
        'schedule': crontab(minute=0, hour='*/6'),   # every 6 hours
    },
    'cleanup-old-webhook-events': {
        'task': 'apps.sync.tasks.cleanup_webhook_events',
        'schedule': crontab(minute=0, hour=3),       # daily at 3 AM
    },
}

app.conf.timezone = 'UTC'
app.conf.task_serializer = 'json'
app.conf.result_serializer = 'json'
app.conf.accept_content = ['json']
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 4
```

### Task Definitions

```python
# apps/sync/tasks.py
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def sync_project(self, project_id: int):
    """Sync a single project's MRs."""
    from apps.projects.models import Project
    from apps.sync.services import SyncService

    project = Project.objects.select_related('source', 'source__credential').get(id=project_id)
    result = SyncService().sync_project(project)
    logger.info(f"Synced {project.full_name}: {result.new_mrs} new, {result.updated_mrs} updated")
    return result


@shared_task(bind=True)
def sync_all_projects(self):
    """Periodic task to sync all active projects."""
    from apps.projects.models import Project

    projects = Project.objects.filter(
        is_active=True,
        source__is_active=True,
        source__sync_enabled=True,
    ).select_related('source', 'source__credential')

    for project in projects:
        sync_project.delay(project.id)


@shared_task(bind=True)
def process_webhook_event(self, event_id: int):
    """Process a received webhook event."""
    from apps.sources.models import WebhookEvent
    from apps.sync.webhook_processor import WebhookProcessor

    event = WebhookEvent.objects.select_related('source').get(id=event_id)
    WebhookProcessor().process(event)


@shared_task(bind=True)
def run_ai_review(self, mr_id: int):
    """Run AI review on a merge request."""
    from apps.projects.models import MergeRequest
    from apps.reviews.pipeline import ReviewPipeline

    mr = MergeRequest.objects.select_related('project', 'project__source').get(id=mr_id)

    if not mr.project.auto_review_enabled:
        logger.info(f"Auto-review disabled for {mr.project.full_name}")
        return
    if mr.state == 'draft' and not mr.project.review_on_draft:
        logger.info(f"Skipping draft MR {mr.external_iid}")
        return

    review = ReviewPipeline().run_review(mr)
    if review.status == 'completed':
        post_review_comments.delay(review.id)


@shared_task(bind=True)
def post_review_comments(self, review_id: int):
    """Post review comments to GitLab/GitHub."""
    from apps.reviews.models import Review
    from apps.sources.registry import AdaptorRegistry

    review = Review.objects.select_related(
        'merge_request', 'merge_request__project', 'merge_request__project__source'
    ).get(id=review_id)

    adaptor = AdaptorRegistry.get_for_source(review.merge_request.project.source)

    for comment in review.comments.all():
        if not comment.external_comment_id:
            ext_id = adaptor.create_comment(
                project=review.merge_request.project,
                mr_iid=review.merge_request.external_iid,
                file_path=comment.file_path,
                line=comment.line_number or 0,
                content=comment.content,
            )
            comment.external_comment_id = ext_id
            comment.save(update_fields=['external_comment_id'])
```

### Webhook Endpoints

```python
# apps/sources/views.py
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.http import JsonResponse
from django.views import View
import json

@method_decorator(csrf_exempt, name='dispatch')
class GitLabWebhookView(View):
    def post(self, request, source_id):
        source = get_object_or_404(GitSource, id=source_id, source_type='gitlab')
        signature = request.headers.get('X-Gitlab-Token') or request.headers.get('X-Gitlab-Signature')
        if not source.webhook_secret or not GitLabAdaptor.verify_webhook(request.body, signature):
            return JsonResponse({'error': 'Invalid signature'}, status=401)

        event = WebhookEvent.objects.create(
            source=source,
            event_type=request.headers.get('X-Gitlab-Event', 'unknown'),
            payload=json.loads(request.body),
            headers=dict(request.headers),
        )
        process_webhook_event.delay(event.id)
        return JsonResponse({'status': 'accepted'})


@method_decorator(csrf_exempt, name='dispatch')
class GitHubWebhookView(View):
    def post(self, request, source_id):
        source = get_object_or_404(GitSource, id=source_id, source_type='github')
        signature = request.headers.get('X-Hub-Signature-256')
        if not source.webhook_secret or not GitHubAdaptor.verify_webhook(request.body, signature):
            return JsonResponse({'error': 'Invalid signature'}, status=401)

        event = WebhookEvent.objects.create(
            source=source,
            event_type=request.headers.get('X-GitHub-Event', 'unknown'),
            payload=json.loads(request.body),
            headers=dict(request.headers),
        )
        process_webhook_event.delay(event.id)
        return JsonResponse({'status': 'accepted'})
```

---

## Security & Credential Management

Follows the [security-review](https://github.com/ECC/security-review) checklist.

### Encryption Service

```python
# apps/credentials/services.py
from cryptography.fernet import Fernet
from django.conf import settings
import base64

class CredentialEncryption:
    """Encrypt/decrypt credentials using Fernet (AES-256)."""

    def __init__(self):
        key = settings.CREDENTIAL_ENCRYPTION_KEY.encode()
        if len(key) != 32:
            key = base64.urlsafe_b64decode(key + b'=' * (-len(key) % 4))
        self.cipher = Fernet(base64.urlsafe_b64encode(key[:32]))

    def encrypt(self, plaintext: str) -> bytes:
        return self.cipher.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        return self.cipher.decrypt(ciphertext).decode()


# Usage in models
class GitSourceCredential(models.Model):
    # ...
    def set_access_token(self, token: str):
        self.access_token_encrypted = CredentialEncryption().encrypt(token)

    def get_access_token(self) -> str:
        return CredentialEncryption().decrypt(self.access_token_encrypted)
```

### Security Checklist

- [ ] All secrets encrypted at rest (Fernet/AES-256)
- [ ] Encryption key from environment variable, never in code
- [ ] HTTPS enforced in production (`SECURE_SSL_REDIRECT=True`)
- [ ] CSRF protection on all forms
- [ ] Webhook signature verification mandatory (no plain-token fallback in prod)
- [ ] No secrets in logs (filter sensitive fields via custom formatter)
- [ ] Database connections use TLS
- [ ] CORS restricted to admin hosts
- [ ] Django security middleware enabled (`SecurityMiddleware`, HSTS, `X-Frame-Options`)
- [ ] Rate limiting on all public endpoints (incl. webhooks)
- [ ] Least-privilege MySQL user (`SELECT/INSERT/UPDATE/DELETE` only, no DDL at runtime)

---

## Django Admin Configuration

Django Admin only (confirmed). All configuration is done through tabbed admin pages.

```python
# apps/admin_extensions/admin.py
from django.contrib import admin
from apps.sources.models import GitSource, GitSourceCredential
from apps.projects.models import Project, MergeRequest
from apps.reviews.models import AIProvider, Review, ReviewComment
from apps.sync.models import SyncLog, WebhookEvent

@admin.register(GitSource)
class GitSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'source_type', 'base_url', 'is_active', 'sync_enabled', 'last_sync']
    list_filter = ['source_type', 'is_active', 'sync_enabled']
    search_fields = ['name', 'base_url']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        (None, {'fields': ('name', 'source_type', 'base_url')}),
        ('Status', {'fields': ('is_active', 'sync_enabled')}),
        ('Webhook', {'fields': ('webhook_secret',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )

    def last_sync(self, obj):
        return obj.sync_logs.first().completed_at if obj.sync_logs.exists() else '-'
    last_sync.short_description = 'Last Sync'


@admin.register(GitSourceCredential)
class GitSourceCredentialAdmin(admin.ModelAdmin):
    list_display = ['source', 'has_token', 'has_app_credentials', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']

    def has_token(self, obj):
        return bool(obj.access_token_encrypted)
    has_token.boolean = True

    def has_app_credentials(self, obj):
        return bool(obj.app_id and obj.private_key_encrypted)
    has_app_credentials.boolean = True


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'source', 'is_active', 'auto_review_enabled', 'last_synced_at', 'mr_count']
    list_filter = ['source', 'is_active', 'auto_review_enabled']
    search_fields = ['name', 'full_name']
    readonly_fields = ['created_at', 'updated_at', 'last_synced_at']
    raw_id_fields = ['source']

    actions = ['enable_auto_review', 'disable_auto_review', 'trigger_sync']

    def mr_count(self, obj):
        return obj.merge_requests.count()

    @admin.action(description='Enable auto-review')
    def enable_auto_review(self, request, queryset):
        queryset.update(auto_review_enabled=True)

    @admin.action(description='Disable auto-review')
    def disable_auto_review(self, request, queryset):
        queryset.update(auto_review_enabled=False)

    @admin.action(description='Trigger sync now')
    def trigger_sync(self, request, queryset):
        from apps.sync.tasks import sync_project
        for project in queryset:
            sync_project.delay(project.id)
        self.message_user(request, f"Triggered sync for {queryset.count()} projects")


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    list_display = ['name', 'provider_type', 'model_name', 'is_active', 'is_default', 'max_tokens_per_request']
    list_filter = ['provider_type', 'is_active', 'is_default']
    search_fields = ['name', 'model_name']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        (None, {'fields': ('name', 'provider_type', 'api_base_url', 'model_name')}),
        ('Authentication', {'fields': ('api_key',)}),          # custom encrypted-input widget
        ('Parameters', {'fields': ('max_tokens_per_request', 'temperature')}),
        ('Prompts', {'fields': ('system_prompt', 'review_prompt_template')}),
        ('Status', {'fields': ('is_active', 'is_default')}),
    )


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['merge_request', 'provider', 'status', 'cost_usd', 'duration_ms', 'created_at']
    list_filter = ['status', 'provider']
    search_fields = ['merge_request__title', 'merge_request__external_iid']
    readonly_fields = ['created_at', 'completed_at', 'input_tokens', 'output_tokens', 'cost_usd', 'duration_ms']
    raw_id_fields = ['merge_request', 'provider']


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ['source', 'project', 'operation', 'status', 'mr_count', 'duration_ms', 'started_at']
    list_filter = ['operation', 'status', 'source']
    readonly_fields = ['started_at', 'completed_at', 'duration_ms']
    date_hierarchy = 'started_at'
```

---

## Implementation Phases

| Phase | Duration | Deliverables | Key Tasks |
|-------|----------|--------------|-----------|
| **Phase 1: Foundation** | 1-2 weeks | Project scaffold, CI/CD, base models | Django project setup, MySQL config, split settings, Docker, Ruff/Black/mypy, GitHub Actions CI |
| **Phase 2: Sources & Credentials** | 1-2 weeks | GitSource, Credential models, encryption, admin UI | Encrypted credential storage, GitLab/GitHub adaptor base, registry pattern, connection testing in admin |
| **Phase 3: Project & MR Sync** | 2-3 weeks | Project/MR models, sync engine, webhook endpoints | Celery setup, periodic sync tasks, webhook receivers (GitLab/GitHub), signature verification, SyncLog audit |
| **Phase 4: AI Review Pipeline** | 2-3 weeks | Multi-provider AI, review models, pipeline | Provider implementations (Anthropic, OpenAI, Ollama, vLLM, Custom), cost tracking, prompt templates, structured parsing |
| **Phase 5: Review Actions** | 1-2 weeks | Post comments, MR state changes | Comment posting to GitLab/GitHub, close/reopen/merge MRs, comment resolution tracking |
| **Phase 6: Admin Polish** | 1 week | Complete Django Admin, dashboards | Custom admin actions, filtering, sync triggers, review history, sync log views |
| **Phase 7: Testing & Hardening** | 1-2 weeks | Test coverage, security, performance | Unit/integration tests, pytest-django, security scan (bandit, pip-audit), load testing sync |

**Total Estimated: 9-15 weeks**

### Phase Detail — First Build

**Phase 1 deliverable list (existing plan's "project bootstrap"):**

- `django-admin startproject config` → split into `config/settings/{base,development,production,test}.py`
- `django-admin startapp` for each app folder under `apps/`
- MySQL via docker-compose (dev), `mysqlclient` driver pinned in `requirements/base.txt`
- Tooling: `ruff`, `black`, `isort`, `mypy`, `pytest-django`, `pytest-cov`, `pip-audit`, `bandit`
- `pyproject.toml` with all tool config
- Dockerfile (multi-stage, python:3.12-slim) + docker-compose (web, worker, beat, flower, mysql)
- CI: GitHub Actions running lint → mypy → tests → security → collectstatic
- Seed `core` app with base mixins (UUID pk, timestamps) and a health check view

---

## Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Async Task Queue** | Celery + Redis | Mature, supports Beat, priority queues, monitoring (Flower) |
| **Encryption** | Fernet (cryptography) | AES-256, authenticated encryption, simple key management |
| **AI Provider Abstraction** | Strategy pattern + registry | Easy to add new providers, testable, follows api-connector-builder |
| **Webhook Processing** | Async via Celery | Non-blocking, retryable, audit trail via WebhookEvent model |
| **Database** | MySQL (InnoDB) | ACID, JSON support for payloads, proven at scale |
| **Admin UI** | Django Admin | Zero frontend maintenance, built-in auth/permissions, sufficient for ops |
| **Cost Control** | CostTracker + budget limits | Prevents runaway spend, per-request estimation |
| **Multi-GitLab Support** | Dynamic registry + per-source config | Scales to many instances, each with own credentials/webhook |
| **Sync** | Webhooks + polling (Beat 15min + 6h stale sweep) | Real-time with fallback, dedup via `external_updated_at` |

---

## Environment Variables

```bash
# Django
DJANGO_SECRET_KEY=your-secret-key-here
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

# Database (MySQL)
DB_HOST=localhost
DB_PORT=3306
DB_NAME=merge_pipeline
DB_USER=merge_pipeline
DB_PASSWORD=secure-password

# Redis (Celery)
REDIS_URL=redis://localhost:6379/0

# Encryption (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
CREDENTIAL_ENCRYPTION_KEY=your-32-byte-base64-key

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1

# AI Providers (optional defaults)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
VLLM_BASE_URL=http://localhost:8000

# Monitoring (optional)
SENTRY_DSN=
FLOWER_URL=http://localhost:5555
```

> Never commit `.env`. Add `*.env` and `.env.*` to `.gitignore`. Provide `.env.example` only.

---

## Verification Checklist

Applied before each release, per the [django-verification](https://github.com/ECC/django-verification) loop:

- [ ] `python manage.py check --deploy` passes
- [ ] `ruff check .` clean
- [ ] `black . --check` clean
- [ ] `mypy .` clean
- [ ] `pytest --cov=apps --cov-fail-under=80` passes
- [ ] `pip-audit` clean
- [ ] `bandit -r .` clean
- [ ] Migrations apply cleanly on a fresh DB (`makemigrations --check`, `migrate --plan`)
- [ ] Webhook signature verification tested (GitLab `X-Gitlab-Token`, GitHub `X-Hub-Signature-256`)
- [ ] Encryption/decryption round-trip works
- [ ] Cost tracking accurate (per-review `input/output_tokens`, `cost_usd`)
- [ ] No N+1 queries on MR list/review list (select_related/prefetch_related)
- [ ] No debugging statements (`print`, `pdb`) in diff

---

## Open Questions

1. **MR Filtering**: Should sync fetch only open MRs, or all states? (Affects initial sync volume)
2. **Diff Size Limits**: Max diff size to send to AI? (Large MRs may exceed context windows)
3. **Review Trigger**: Auto-review on every push to MR, or only on specific events (`ready_for_review`, label added)?
4. **Comment Format**: Post as inline diff comments or general MR comments? (GitLab/GitHub APIs differ)
5. **Multi-tenancy**: Will multiple teams use this? (May need organization/team model)
6. **Notifications**: Email/Slack on review complete? (Separate notification service?)
7. **API Rate Limits**: GitLab/GitHub have different limits — need adaptive concurrency?
8. **Migration Strategy**: Existing MR data to import? (One-time backfill script?)

---

## Suggested First Steps (Phase 1 kickoff)

1. Bootstrap Django project + split settings
2. Add MySQL + Redis via docker-compose
3. Write `pyproject.toml` with ruff/black/mypy/pytest configuration
4. Create base models (`core` app) and health-check endpoint
5. Set up GitHub Actions CI lane
6. Create the four Django admin stub registrations (sources, projects, reviews, sync)
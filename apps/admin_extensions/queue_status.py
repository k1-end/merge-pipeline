"""Read-only operational snapshot of the Celery queue."""

import ast
import json
from collections.abc import Callable
from datetime import datetime
from threading import Lock
from typing import Any, cast

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from redis import Redis

from config.celery import app as celery_app

TaskPayload = dict[str, Any]
WorkerTasks = dict[str, list[TaskPayload]]

_SNAPSHOT_CACHE_KEY = "admin_extensions:queue_status:v1"
_snapshot_lock = Lock()

_REVIEW_TASK_NAMES = {
    "apps.reviews.tasks.execute_ai_review",
    "apps.reviews.tasks.execute_manual_ai_review",
    "apps.reviews.tasks.run_ai_review",
    "apps.reviews.tasks.run_manual_ai_review",
}

_TASK_DETAILS: dict[str, tuple[str, str | None]] = {
    "apps.reviews.tasks.clone_repository_cache": ("Clone repository cache", "project"),
    "apps.reviews.tasks.execute_ai_review": ("Automatic AI review", "review"),
    "apps.reviews.tasks.execute_manual_ai_review": ("Manual AI review", "review"),
    "apps.reviews.tasks.run_ai_review": ("Automatic AI review", "merge_request"),
    "apps.reviews.tasks.run_manual_ai_review": ("Manual AI review", "merge_request"),
    "apps.reviews.tasks.post_review_comments": ("Publish review comments", "review"),
    "apps.sync.tasks.sync_project": ("Sync project", "project"),
    "apps.sync.tasks.sync_all_projects": ("Sync all projects", None),
    "apps.sync.tasks.sync_stale_projects": ("Sync stale projects", None),
    "apps.sync.tasks.process_webhook_event": ("Process webhook", "webhook"),
    "apps.sync.tasks.cleanup_webhook_events": ("Clean up webhooks", None),
}


class QueueJobNotFoundError(LookupError):
    """Raised when a queue job disappeared before cancellation."""


class QueueJobNotCancellableError(ValueError):
    """Raised when an operator tries to cancel a non-review task."""


class QueueControlUnavailableError(RuntimeError):
    """Raised when Celery control cannot inspect or revoke work."""


def _broker_queue_depth() -> int:
    """Return the number of messages still waiting in the default Redis queue."""
    broker_url = settings.CELERY_BROKER_URL
    if not broker_url.startswith(("redis://", "rediss://")):
        raise ValueError("Queue depth is only available for the configured Redis broker")
    queue_name = celery_app.conf.task_default_queue or "celery"
    client = Redis.from_url(
        broker_url,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )
    try:
        return cast(int, client.llen(queue_name))
    finally:
        client.close()


def _inspect(inspector: Any, command: str) -> tuple[WorkerTasks, bool]:
    """Run one inspect command without allowing worker failures to reach the view."""
    if inspector is None:
        return {}, False
    try:
        response = getattr(inspector, command)()
    except Exception:  # noqa: BLE001 - this boundary reports unavailable infrastructure
        return {}, False
    if response is None:
        return {}, False
    return response, True


def _first_integer_argument(payload: TaskPayload) -> int | None:
    """Read the first numeric task argument without exposing or evaluating arbitrary code."""
    args = payload.get("args", [])
    if isinstance(args, str):
        if len(args) > 500:
            return None
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            try:
                args = ast.literal_eval(args)
            except (SyntaxError, ValueError):
                return None
    if not isinstance(args, list | tuple) or not args:
        return None
    value = args[0]
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _task_label(task_name: str) -> tuple[str, str | None]:
    """Return an operator-facing task label and optional target model kind."""
    details = _TASK_DETAILS.get(task_name)
    if details is not None:
        return details
    short_name = task_name.rsplit(".", maxsplit=1)[-1]
    return short_name.replace("_", " ").strip().title() or "Celery task", None


def _normalize_jobs(
    responses: WorkerTasks,
    state: str,
    now: datetime,
    payload_getter: Callable[[TaskPayload], TaskPayload] | None = None,
) -> list[dict[str, Any]]:
    """Convert inspect payloads into the intentionally small public job shape."""
    jobs: list[dict[str, Any]] = []
    for worker, tasks in responses.items():
        for raw_payload in tasks:
            payload = payload_getter(raw_payload) if payload_getter else raw_payload
            if not isinstance(payload, dict):
                continue
            task_name = str(payload.get("name", ""))
            label, target_kind = _task_label(task_name)
            job: dict[str, Any] = {
                "id": str(payload.get("id", "")),
                "task": label,
                "state": state,
                "target": "",
                "worker": worker,
                "duration_seconds": None,
                "cancellable": task_name in _REVIEW_TASK_NAMES and bool(payload.get("id")),
            }
            target_id = _first_integer_argument(payload)
            if target_kind and target_id is not None:
                job["_target_kind"] = target_kind
                job["_target_id"] = target_id
            if state == "running":
                started_at = payload.get("time_start")
                if isinstance(started_at, int | float):
                    job["duration_seconds"] = max(0, int(now.timestamp() - started_at))
            if state == "scheduled":
                job["scheduled_for"] = raw_payload.get("eta")
            jobs.append(job)
    return jobs


def _enrich_targets(jobs: list[dict[str, Any]]) -> None:
    """Resolve internal numeric arguments into safe project and merge-request labels."""
    from apps.projects.models import MergeRequest, Project
    from apps.reviews.models import Review

    ids_by_kind: dict[str, set[int]] = {}
    for job in jobs:
        kind = job.get("_target_kind")
        target_id = job.get("_target_id")
        if isinstance(kind, str) and isinstance(target_id, int):
            ids_by_kind.setdefault(kind, set()).add(target_id)

    projects = Project.objects.in_bulk(ids_by_kind.get("project", set()))
    merge_requests = {
        merge_request.pk: merge_request
        for merge_request in MergeRequest.objects.select_related("project").filter(
            pk__in=ids_by_kind.get("merge_request", set())
        )
    }
    reviews = {
        review.pk: review
        for review in Review.objects.select_related("merge_request__project").filter(
            pk__in=ids_by_kind.get("review", set())
        )
    }

    for job in jobs:
        kind = job.pop("_target_kind", None)
        target_id = job.pop("_target_id", None)
        if kind == "project" and target_id in projects:
            job["target"] = projects[target_id].full_name
        elif kind == "merge_request" and target_id in merge_requests:
            merge_request = merge_requests[target_id]
            job["target"] = f"{merge_request.project.full_name} !{merge_request.external_iid}"
        elif kind == "review" and target_id in reviews:
            review = reviews[target_id]
            job["target"] = f"{review.merge_request.project.full_name} !{review.merge_request.external_iid}"
        elif kind == "webhook" and isinstance(target_id, int):
            job["target"] = f"Webhook event #{target_id}"


def get_queue_snapshot() -> dict[str, Any]:
    """Return worker health, queue counts, and safe details for visible jobs."""
    timeout = getattr(settings, "CELERY_INSPECT_TIMEOUT_SECONDS", 0.5)
    try:
        inspector = celery_app.control.inspect(timeout=timeout)
    except Exception:  # noqa: BLE001 - snapshot degrades when control cannot initialize
        inspector = None

    active, active_ok = _inspect(inspector, "active")
    reserved, reserved_ok = _inspect(inspector, "reserved")
    scheduled, scheduled_ok = _inspect(inspector, "scheduled")

    try:
        broker_depth = _broker_queue_depth()
        broker_ok = True
    except Exception:  # noqa: BLE001 - broker outages are reported in the snapshot
        broker_depth = 0
        broker_ok = False

    now = timezone.now()
    jobs = _normalize_jobs(active, "running", now)
    jobs.extend(_normalize_jobs(reserved, "waiting", now))
    jobs.extend(
        _normalize_jobs(
            scheduled,
            "scheduled",
            now,
            payload_getter=lambda payload: payload.get("request", {}),
        )
    )
    jobs = jobs[:50]
    _enrich_targets(jobs)

    workers = set(active) | set(reserved) | set(scheduled)
    inspect_ok = active_ok and reserved_ok and scheduled_ok
    if workers and inspect_ok and broker_ok:
        health = "online"
    elif any((active_ok, reserved_ok, scheduled_ok, broker_ok)):
        health = "degraded"
    else:
        health = "unavailable"

    reserved_count = sum(len(tasks) for tasks in reserved.values())
    return {
        "health": health,
        "counts": {
            "workers": len(workers),
            "running": sum(len(tasks) for tasks in active.values()),
            "waiting": reserved_count + broker_depth,
            "scheduled": sum(len(tasks) for tasks in scheduled.values()),
        },
        "jobs": jobs,
        "updated_at": now.isoformat(),
    }


def get_cached_queue_snapshot() -> dict[str, Any]:
    """Coalesce nearby dashboard polls into one infrastructure inspection."""
    cached = cache.get(_SNAPSHOT_CACHE_KEY)
    if isinstance(cached, dict):
        return cached

    with _snapshot_lock:
        cached = cache.get(_SNAPSHOT_CACHE_KEY)
        if isinstance(cached, dict):
            return cached
        snapshot = get_queue_snapshot()
        timeout = getattr(settings, "QUEUE_STATUS_CACHE_SECONDS", 3)
        cache.set(_SNAPSHOT_CACHE_KEY, snapshot, timeout=timeout)
        return snapshot


def cancel_review_job(task_id: str) -> dict[str, str]:
    """Revoke one currently visible AI-review task and persist cancellation state."""
    timeout = getattr(settings, "CELERY_INSPECT_TIMEOUT_SECONDS", 0.5)
    try:
        inspector = celery_app.control.inspect(timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - infrastructure failures become an operator response
        raise QueueControlUnavailableError("Queue control is unavailable.") from exc
    if inspector is None:
        raise QueueControlUnavailableError("Queue control is unavailable.")

    inspections = (
        ("running", "active", None),
        ("waiting", "reserved", None),
        ("scheduled", "scheduled", lambda item: item.get("request", {})),
    )
    any_inspection_succeeded = False
    matched: tuple[str, str, TaskPayload] | None = None
    for state, command, payload_getter in inspections:
        responses, succeeded = _inspect(inspector, command)
        any_inspection_succeeded = any_inspection_succeeded or succeeded
        for worker, tasks in responses.items():
            for raw_payload in tasks:
                payload = payload_getter(raw_payload) if payload_getter else raw_payload
                if isinstance(payload, dict) and str(payload.get("id", "")) == task_id:
                    matched = (state, worker, payload)
                    break
            if matched is not None:
                break
        if matched is not None:
            break

    if matched is None:
        if not any_inspection_succeeded:
            raise QueueControlUnavailableError("Queue control is unavailable.")
        raise QueueJobNotFoundError("This queue job has already finished or disappeared.")

    state, worker, payload = matched
    if payload.get("name") not in _REVIEW_TASK_NAMES:
        raise QueueJobNotCancellableError("Only AI-review jobs can be cancelled here.")

    revoke_kwargs: dict[str, Any] = {
        "terminate": state == "running",
        "destination": [worker],
    }
    if state == "running":
        revoke_kwargs["signal"] = "SIGTERM"
    try:
        celery_app.control.revoke(task_id, **revoke_kwargs)
    except Exception as exc:  # noqa: BLE001 - infrastructure failures become an operator response
        raise QueueControlUnavailableError("The queue job could not be cancelled.") from exc

    from apps.reviews.models import Review

    Review.objects.filter(
        celery_task_id=task_id,
        status__in=(Review.PENDING, Review.PROCESSING),
    ).update(
        status=Review.CANCELLED,
        error_message="Cancelled by an operator.",
        completed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    cache.delete(_SNAPSHOT_CACHE_KEY)
    return {"id": task_id, "state": state}

# Live Queue Dashboard — TDD Evidence

## Source

The user journey and acceptance criteria were derived from the implementation discussion; no plan file was used.

## User journey

As a staff operator, I want to see live Celery worker and job status on the operations dashboard so that I can identify active work and queue bottlenecks without opening a separate tool.

## Execution evidence

| Stage | Command | Result |
|---|---|---|
| RED | `python -m pytest apps/admin_extensions/test_queue_status.py -q` | Collection failed with `ModuleNotFoundError: apps.admin_extensions.queue_status`, proving the service was absent. |
| GREEN | `python -m pytest apps/admin_extensions/test_queue_status.py -q` | 6 passed. |
| Regression | `python -m pytest -q` | 71 passed, 7 subtests passed. |
| Lint | `ruff check .` | All checks passed. |
| Coverage | `python -m pytest --cov=apps --cov=config --cov-report=term -q` | 83.39% total coverage; required 80% reached. |
| Focused typing | `mypy apps/admin_extensions/queue_status.py apps/admin_extensions/test_queue_status.py` | No issues found. |

The commands above were run with the existing project virtual environment at `/home/vibox19/Projects/zoodex/merge-pipeline/.venv`.

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | Running and waiting jobs, worker count, and broker depth are summarized correctly. | `QueueSnapshotTests.test_snapshot_reports_workers_and_safe_job_details` | Unit/integration | PASS |
| 2 | Raw Celery arguments and keyword arguments are not returned to the browser. | `QueueSnapshotTests.test_snapshot_reports_workers_and_safe_job_details` | Security | PASS |
| 3 | Project and merge-request IDs are resolved to operator-friendly labels. | `QueueSnapshotTests.test_snapshot_reports_workers_and_safe_job_details` | Integration | PASS |
| 4 | Worker and broker outages return an unavailable snapshot instead of breaking the dashboard. | `QueueSnapshotTests.test_snapshot_is_unavailable_when_broker_and_workers_are_offline` | Failure path | PASS |
| 5 | Anonymous users cannot access queue operational data. | `QueueStatusEndpointTests.test_anonymous_users_cannot_read_queue_status` | Authorization | PASS |
| 6 | Staff receive JSON with browser caching disabled. | `QueueStatusEndpointTests.test_staff_users_receive_queue_snapshot` | Endpoint | PASS |
| 7 | The dashboard contains the live queue panel and polling endpoint. | `QueueStatusEndpointTests.test_dashboard_contains_live_queue_panel` | View | PASS |
| 8 | Nearby dashboard polls reuse a recent snapshot instead of repeating Celery broadcasts. | `QueueSnapshotTests.test_cached_snapshot_coalesces_repeated_dashboard_polls` | Performance | PASS |

## Known gaps

- Browser polling uses safe DOM APIs and a four-second abort deadline but is not covered by a browser-level test.
- Redis queue depth targets the configured default queue; custom additional Celery queues are outside this feature's scope.
- Full-project `mypy apps config` still reports pre-existing errors outside this feature. The new queue service and tests pass focused type checking.

## Checkpoints

- RED: `d8897a6 test: define live queue dashboard behavior`
- GREEN: `2163ae0 feat(dashboard): show live Celery queue status`
- Resilience RED: `e598ae8 test: cover queue polling resilience`

# Sync Cursor Race — TDD Evidence

## Source

The user journey and acceptance criteria were derived from the reported missing GitLab merge request; no plan file was used.

## User journey

As an operator, I want merge requests created while a project sync is running to be imported by the next incremental sync so that no request is permanently skipped.

## Execution evidence

| Stage | Command | Result |
|---|---|---|
| RED | `.venv/bin/pytest apps/sync/tests.py::test_sync_project_does_not_skip_merge_request_created_during_previous_sync -q` | Failed because the second sync used the first sync's completion time and did not import MR `2661`. |
| GREEN | `.venv/bin/pytest apps/sync/tests.py::test_sync_project_does_not_skip_merge_request_created_during_previous_sync -q` | 1 passed. |
| Sync regression | `.venv/bin/pytest apps/sync/tests.py -q` | 9 passed. |
| Full regression | `.venv/bin/pytest -q` | 109 passed, 7 subtests passed. |
| Lint | `.venv/bin/ruff check apps/sync/services.py apps/sync/tests.py` | All checks passed. |
| Coverage | `.venv/bin/pytest --cov=apps --cov-report=term -q` | 84.57% total coverage; required 80% reached. |

## Test specification

| # | Guarantee | Test | Type | Result |
|---|---|---|---|---|
| 1 | A sync advances its incremental cursor only to the time at which that sync started. | `apps/sync/tests.py::test_sync_project_does_not_skip_merge_request_created_during_previous_sync` | Integration | PASS |
| 2 | An MR created after the provider fetch begins but before that sync finishes is imported on the next sync. | `apps/sync/tests.py::test_sync_project_does_not_skip_merge_request_created_during_previous_sync` | Regression | PASS |
| 3 | Existing sync, webhook, reconciliation, and automatic-review behavior remains intact. | `apps/sync/tests.py` | Regression | PASS |

## Known gaps

- This fix prevents cursor gaps but does not serialize overlapping sync tasks; overlapping runs can still perform duplicate idempotent work.
- The existing files are not fully Black-formatted, and focused mypy reports pre-existing errors in `apps/reviews/tasks.py` and existing callback lambdas in `apps/sync/services.py`.

## Checkpoints

- RED: `33edaa9 test(sync): reproduce merge request cursor race`
- GREEN: recorded in the subsequent fix commit.

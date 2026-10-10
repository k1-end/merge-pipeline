# Inline-only review comments — evidence

## Source and journeys

Requirements came from the conversation: publish every finding inline; recover
missing locations through same-file and then cross-file anchors; label fallbacks;
block only when safe inline publication is unavailable. No general-comment fallback.

Operator journeys: inspect blocked findings, correct file/line/side in Django
admin, retry publication without another AI execution, and see partial progress.

## RED evidence

- `.venv/bin/pytest apps/reviews/test_locations.py -q`: collection failed because
  the required `apps.reviews.locations` implementation did not exist.
- `.venv/bin/pytest apps/reviews/test_locations.py apps/sources/test_inline.py -q`:
  initial provider tests produced **4 failed, 10 passed**. Existing implementations
  used general endpoints and could not recover inline markers.
- The same target after adding old-side context cases produced **2 failed,
  17 passed**: old-side context coordinates were lost.
- `.venv/bin/pytest apps/reviews/tests.py -k 'inline_blocking or post_comments_fallbacks or parser_preserves' -q`:
  **1 failed, 8 passed**; provider position rejection did not persist blocked state.
- `.venv/bin/pytest apps/admin_extensions/tests.py -k review_detail_renders_execution_context -q`:
  **1 failed**; incomplete publication and blocked reasons were absent from the page.
- `.venv/bin/pytest apps/reviews/tests.py -k transient_publication -q`:
  **1 failed**; a transient creation error prevented partial progress.
- `.venv/bin/pytest apps/reviews/tests.py -k opencode_provider_invokes_read_only_agent -q`:
  **1 failed**; the agent output contract lacked an explicit diff side.

Task checkpoint commits: `0222441`, `a5e766f`, `94c9cec`, `1d56ff8`, `fe652da`,
and `653b033`. These preserve task-local RED evidence on the active branch.
Some additional defensive/parser/snapshot tests were added after implementation;
they are regression evidence, not claimed as separate RED cycles.

## GREEN guarantees

| Guarantee | Test target | Type | Result |
|---|---|---|---|
| Exact locations; missing/invalid lines; deleted/renamed files; cross-file anchors; incomplete hunks | `apps/reviews/test_locations.py` | Unit | PASS |
| Provider inline payloads, old-side coordinates, marker recovery, stable snapshots, missing-position rejection | `apps/sources/test_inline.py` | Unit with mocked API | PASS |
| Locationless findings preserved; stale/legacy/binary/rejected positions blocked; labelled fallbacks | `apps/reviews/tests.py` | Database integration | PASS |
| Agent workspace head/target revisions match the captured snapshot | `test_opencode_workspace_matches_snapshot` | Integration with mocked workspace | PASS |
| Partial creation failures persist errors, trigger task retry, and clear after successful retry | `test_transient_publication_failure_preserves_partial_success` | Database/task integration | PASS |
| Publication incompleteness and blocking reasons are visible | `apps/admin_extensions/tests.py` | Django HTTP/rendering integration | PASS |

Final command: `.venv/bin/pytest --cov --cov-report=term -q`

Result: **161 passed, 7 subtests passed**, four existing Django URL-field
deprecation warnings. Configured coverage: **86.32%** (the existing configuration
includes test modules). Location resolver coverage is 91%; pipeline and review
services are each 90%.

## Other checks and gaps

- Ruff on changed Python files: **All checks passed**.
- Black check on 18 changed Python files: **18 files would be left unchanged**.
- `.venv/bin/python manage.py check --settings=config.settings.test`:
  **System check identified no issues**.
- `.venv/bin/python manage.py makemigrations --check --dry-run --settings=config.settings.test`:
  **No changes detected**. Migration exercised by the test database, not applied
  to the configured development/production database.
- Mypy on eight changed production modules reports two existing missing-return
  errors in `apps/reviews/tasks.py:121` and `:163`; those execution functions were
  not functionally changed by this task.
- No live GitLab/GitHub posts, production database changes, browser E2E, or
  multi-worker concurrency test was performed. API tests use mocks; database
  locking must be validated on the deployment database for concurrency proof.

Behavior and rollout instructions: [inline review comments](../../docs/inline-review-comments.md).

# Automatic review comments — TDD evidence

Journeys were derived from the request: operators opt in per project to publish
AI findings automatically, independently of automatic review execution.

## Validation

- RED: `.venv/bin/pytest apps/reviews/tests.py -k 'auto_post or post_comments_task_checks' -q`
  produced **9 failed, 4 passed** before implementation: missing project setting,
  missing form field, missing completion callback, and missing task eligibility gates.
- GREEN: the same command produced **13 passed** after implementation.
- Full suite: `.venv/bin/pytest --cov --cov-report=term-missing -q` produced
  **125 passed**, **7 subtests passed**, **84.97%** total coverage. After extending
  the form test to verify persistence, `.venv/bin/pytest --cov --cov-report=term -q`
  again produced **125 passed**, **7 subtests passed**, **84.99%** total coverage.
- Migration consistency: `.venv/bin/python manage.py makemigrations --check --dry-run --settings=config.settings.test`
  produced **No changes detected**.

## Guarantees

| Guarantee | Tests in `apps/reviews/tests.py` |
| --- | --- |
| Projects default to opt-out | `test_auto_post_comments_setting_defaults_off` |
| Project edit form exposes and saves the toggle | `test_project_form_exposes_auto_post_comments` |
| Only opted-in successful findings queue posting after commit | `test_pipeline_auto_post_comments` (five cases) |
| Failed, cancelled, and empty reviews do not queue posting | `test_pipeline_auto_post_comments` |
| Broker failure preserves completed review and findings and logs the error | `test_auto_post_broker_failure_keeps_review_completed` |
| Posting checks persisted project setting and completed status; manual posting remains available | `test_post_comments_task_checks_status_and_auto_setting` (five cases) |

## Known gaps

External AI, broker, and provider writes are mocked; no live remote comments were
created. Broker enqueue failures are logged and require manual posting, not a
durable outbox. Existing duplicate-marker logic is reused unchanged. No browser
E2E or production migration was run. Changes are left uncommitted.

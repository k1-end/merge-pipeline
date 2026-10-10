# Snapshot-driven review regression evidence

## Intent

Derived from the project-sync/review failure investigation, not a plan file.
Review the current MR snapshot consistently even when branches move during the
run, while allowing later reviews to inspect new source commits.

## RED → GREEN

- RED: `.venv/bin/pytest apps/reviews/tests.py -k 'workspace_uses_persistent_cache or workspace_matches_snapshot' -q`
  produced **7 failed, 1 passed**. Failures exposed missing pinned-SHA workspace
  inputs and missing persisted snapshots for failed reviews.
- GREEN: `.venv/bin/pytest apps/reviews/tests.py -q` initially produced **54 passed**.
  Additional regression cases were subsequently added.
- Expanded verification: `.venv/bin/pytest apps/reviews/tests.py apps/sources/test_inline.py apps/sync/tests.py -q`
  produced **85 passed**.
- Full verification: `.venv/bin/pytest --cov=apps --cov=config --cov-report=term-missing -q`
  produced **190 passed, 7 subtests passed**, with **86.77% overall coverage**.
  The changed pipeline and workspace modules had **90%** and **94%** coverage.
  Four existing Django URLField deprecation warnings remained.

## Guarantees

| Guarantee | Test | Type |
| --- | --- | --- |
| Original source and target commits survive advancement, force-push, or branch deletion when available | `test_review_workspace_uses_persistent_cache` | Real Git integration |
| Later snapshots fetch and check out newly pushed commits | Same test, movement cases | Real Git integration |
| Concurrent workspaces are isolated and clean up independently | Same test, nested workspaces | Real Git integration |
| Missing commits never fall back to latest branch tips | Same test, missing case | Real Git integration |
| Inconsistent merge bases fail explicitly | Same test, invalid-base case | Real Git integration |
| Mutable refs, malformed SHAs, and options are rejected | `test_workspace_rejects_non_commit_revisions` | Unit |
| GitLab position SHAs remain unchanged; workspace mismatches retain snapshots and prevent AI execution | `test_opencode_workspace_matches_snapshot` | Database integration |
| Stale snapshots cannot publish new inline comments | Existing `test_inline_blocking_is_visible` | Database integration |
| New source SHAs queue subsequent reviews | Existing `test_sync_project_queues_auto_review_after_commit_for_new_revision` | Database integration |

## Live container check

Using `docker exec merge-pipeline-dev-worker-1 python manage.py shell -c ...`,
fetched MR !2708's current GitLab snapshot and prepared a disposable pinned
workspace with target `7c6f989911f4d17ddc444922046634d8e472a532` and head
`4b8032274cd5c40272671e87600e59898bd73985`. Preparation and cleanup succeeded.
No paid AI request, review-row mutation, or comment publication was performed.

## Scope and gaps

No schema changes, automatic integration review on target-only updates, automatic
stale-review requeue, commits, or deployment restarts. Real provider execution and
remote inline publication were not performed. Exact-SHA retrieval depends on the
remote server allowing retrieval of missing objects; otherwise the review fails
explicitly instead of inspecting a different commit.

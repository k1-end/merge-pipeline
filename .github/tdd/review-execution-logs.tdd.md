# Review execution logs — TDD evidence

## Source and user journey

No plan file was used. The journey was derived from the request: as a staff operator, I can inspect and download every byte emitted by an AI review provider, including failed OpenCode runs, without application-level truncation.

## Evidence

| Guarantee | Test/evidence | Result |
|---|---|---|
| OpenCode stdout and stderr are retained above the former limit and on non-zero exit | `apps/reviews/tests.py` provider log tests | PASS |
| Arbitrary provider bytes round-trip exactly in ordered chunks | `test_execution_log_recorder_preserves_arbitrary_bytes` | PASS |
| Failed reviews and their logs remain persisted | `test_pipeline_persists_all_log_chunks_for_failed_review` | PASS |
| Log-storage setup failure cannot leave a review processing | `test_pipeline_marks_review_failed_when_log_setup_fails` | PASS |
| Staff can view and download all chunks across the 500-row query boundary | `test_review_detail_embeds_complete_execution_log` | PASS |
| Anonymous users cannot access review logs | `test_review_execution_log_requires_staff` | PASS |

## RED/GREEN checkpoints

- RED `6f4406c`: missing execution-log model caused test collection failure.
- GREEN `a37acaf`: provider capture, persistence, migration, and dashboard viewer passed targeted tests.
- RED `c356fce`: byte round-trip test exposed lossy text storage.
- GREEN/refactor `003da2b`: binary chunks, disk spooling, and keyset streaming passed the full suite.
- RED `4dbcf73`: cache policy and logging-setup failure tests reproduced the remaining gaps.
- GREEN `b3b86b1`: cache prevention and failure-safe logging passed; follow-up OCR review reported zero findings.

## Final validation

- `pytest --cov=apps --cov=config --cov-report=term -q`: **72 passed**, coverage **84.29%**.
- `ruff check apps config`: PASS.
- `python manage.py check` with test settings: PASS.
- `python manage.py makemigrations --check --dry-run` with test settings: no changes detected.

The only observed warnings are Django's existing `URLField.assume_scheme` deprecation warnings.

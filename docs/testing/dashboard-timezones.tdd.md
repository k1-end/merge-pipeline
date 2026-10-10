# Dashboard server timezone

User journey: operators see every dashboard timestamp in the server timezone,
regardless of their browser or an activated user timezone.

## Evidence

- RED: `.venv/bin/pytest apps/admin_extensions/test_timezones.py::test_dashboard_dates_and_live_clock_use_server_timezone -q`
  failed because the expected `Jan 2, 01:30` server-local timestamp was absent.
- GREEN: `.venv/bin/pytest apps/admin_extensions/tests.py apps/admin_extensions/test_queue_status.py apps/admin_extensions/test_timezones.py -q`
  passed before expanding the timezone matrix (57 tests, 7 subtests).
- Final: `.venv/bin/pytest --cov --cov-report=term -q` passed
  (171 tests, 7 subtests; 86.50% overall coverage).
- `.venv/bin/ruff check config/timezones.py apps/admin_extensions/context_processors.py apps/admin_extensions/test_timezones.py config/settings/base.py`
  passed; `git diff --check` passed.

## Guarantees

| Behavior | Regression coverage |
| --- | --- |
| Templates override an activated user timezone with the server zone | `test_dashboard_dates_and_live_clock_use_server_timezone` |
| UTC, Tehran's fractional offset, Berlin winter/summer offsets and date rollover | Same parametrized test |
| Live clock receives the server IANA zone and uses an explicit `timeZone` option | Same test, rendered script assertions |
| System detection honors `TZ`, localtime symlink and timezone file | `test_system_timezone_detection` |
| Missing/invalid system configuration falls back to UTC | Detection tests and `test_system_timezone_missing_files` |

The new Python modules have 100% statement coverage. No browser E2E test was run;
live-clock coverage checks the rendered JavaScript configuration. Existing Django
URL-field deprecation warnings remain. RED/GREEN evidence is preserved here rather
than in separate checkpoint commits.

`TIME_ZONE` explicitly overrides system detection. System configuration is read
at application startup; restart application processes after changing it. Containers
use their own system timezone; configure `TIME_ZONE` if it should match the host.

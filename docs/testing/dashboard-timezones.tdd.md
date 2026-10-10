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
at application startup; recreate application containers after changing the host
timezone. Compose now supplies host timezone files as described below.

## Docker host timezone inheritance follow-up

Journey: application containers automatically load the Docker host's timezone,
without setting a timezone manually in `.env`.

- RED: `.venv/bin/pytest apps/admin_extensions/test_timezones.py -q` reported
  3 failures and 11 passes: both Compose files lacked mounts, and an image UTC
  symlink took precedence over the host's timezone name.
- Fix: read-only `/etc/localtime` and `/etc/timezone` mounts on all four application
  services in both Compose files; prefer the timezone file over the localtime
  symlink name. The Linux host must provide both files. Explicit environment
  overrides remain supported.
- GREEN: `.venv/bin/pytest --cov --cov-report=term -q` passed 175 tests and
  7 subtests, with 86.52% overall coverage.
- Both `docker compose -f .docker/{dev,prod}/docker-compose.yml config --quiet`
  validations passed (run separately).
- Rebuilt/recreated the dev web, worker, size-labeling worker and beat services
  using `docker compose -f .docker/dev/docker-compose.yml up -d --build --no-deps web worker size-labeling-worker beat`.
- Verified each running service's Django `TIME_ZONE` is `Asia/Tehran`, the rendered
  dashboard contains the same browser clock timezone, and the web returns HTTP 200.
- Production configuration was validated but not deployed. MySQL and Redis were
  not recreated. No browser E2E test was run.

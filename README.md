# Merge Pipeline

Manage merge requests across many repositories, each living on exactly one origin host — an on-premise GitLab, `gitlab.com`, or GitHub. Fetches MRs via webhooks + polling (Celery Beat), reviews them with AI (multi-provider), posts/resolves review comments, and can open/close/merge MRs. All configuration lives in Django Admin; data is stored in MySQL.

> Full design: see [PLAN.md](./PLAN.md)

## Phase Status

- [x] **Phase 1 — Foundation**: Django scaffold, split settings, MySQL + Celery wiring, Docker, tooling (ruff/black/mypy/pytest/bandit/pip-audit), CI
- [ ] Phase 2 — Sources & Credentials
- [ ] Phase 3 — Project & MR Sync + Webhooks
- [ ] Phase 4 — AI Review Pipeline
- [ ] Phase 5 — Review Actions
- [ ] Phase 6 — Admin Polish
- [ ] Phase 7 — Testing & Hardening

## Quick Start (development)

Prerequisites: MySQL 8 and Redis reachable (or use docker-compose).

```bash
# 1. Environment
cp .env.example .env
# edit .env — set DB_PASSWORD, CREDENTIAL_ENCRYPTION_KEY, etc.

# 2. Virtualenv
python -m venv .venv
. .venv/bin/activate
pip install -r requirements/development.txt

# 3. Database
python manage.py migrate
python manage.py createsuperuser

# 4. Run
python manage.py runserver            # web + admin at http://localhost:8000
celery -A config worker -l info       # worker (separate terminal)
celery -A config worker -l info -Q size-labeling  # size-label worker
celery -A config beat -l info         # scheduler (separate terminal)
```

## Docker (alternative)

```bash
cp .env.example .env
docker compose -f .docker/dev/docker-compose.yml up --build
```

Both Compose configurations automatically inherit the Linux Docker host's timezone
by mounting `/etc/localtime` and `/etc/timezone` read-only into the web, worker,
size-labeling worker, and scheduler containers. The host must provide both files,
with an IANA timezone name in `/etc/timezone` (for example, `Asia/Tehran`). No
hardcoded timezone is needed. An explicit `TIME_ZONE` in the application's
environment still overrides automatic detection. Recreate the application
containers after changing the host timezone.

## Tooling

```bash
ruff check .            # lint
black . --check         # formatting
isort . --check-only    # import order
mypy .                  # type checking
pytest                  # tests (DJANGO_SETTINGS_MODULE=config.settings.test)
python manage.py check  # Django system checks
bandit -r apps config   # security lint
pip-audit               # dependency vulnerabilities
```

## Layout

```
config/            project settings (split by env) + Celery
apps/core/         base mixins + health checks
apps/sources/      git hosts (GitLab/GitHub) + webhooks
apps/projects/     repositories + merge requests
apps/reviews/      AI providers + review pipeline
apps/sync/         Celery tasks + webhook processors
apps/sizing/       configurable MR size classification + label worker
apps/credentials/  encrypted credentials
apps/admin_extensions/  custom admin
```

## Merge-request size labels

The dedicated `size-labeling` Celery worker publishes one of `size/XS`, `size/S`,
`size/M`, `size/L`, or `size/XL` to GitLab merge requests and GitHub pull requests.
Sizes use additions plus deletions. Staff can edit the global thresholds from
**Configuration → Size labels** in the operations dashboard. Saving a changed policy
or using **Relabel open requests** queues open and draft requests for reclassification;
unrelated provider labels are preserved.

## OpenCode Reviews

OpenCode reviews run in the Celery worker as a read-only agent against a disposable
Git worktree of the merge request source branch. The worker image must contain `git`
and the OpenCode CLI. Configure the worker's OpenCode credentials in its dedicated
`~/.local/share/opencode` data directory; do not mount a developer's home directory
into the worker. `OPENCODE_CONFIG_DIR` is optional and controls extra configuration.

Create an OpenCode provider in the admin dashboard with provider type `OpenCode
Agent` and a model in `provider/model` format, for example
`opencode/deepseek-v4.1-flash`. The selected project must have a clone URL and a
configured Git source credential. Enable `auto_review_enabled` only after testing
the generated reviews manually.

Before the first review, open **Projects** and click **Clone cache** for the repository.
The clone runs asynchronously in the Celery worker and is stored as a bare repository
in a Docker-managed named volume. Repository caches are never bind-mounted into the
host checkout. Reviews refuse to run when the cache is missing; they fetch the requested
branch into the cache, create a temporary worktree, and remove that worktree afterward.

The bundled `.opencode/agents/review.md` agent denies edits, web access, subagents,
and arbitrary shell commands while allowing read-only Git inspection. Each review
fetches and pins both MR branches, then compares the target branch to the feature
branch with three-dot diff semantics. Review output is expected to be JSON and is validated by the
same parser used by the HTTP providers.

To automatically publish findings, edit the project and enable **Automatically post
AI review comments** (`auto_post_comments_enabled`). This setting is off by default
and independent of `auto_review_enabled`: it applies to successful manual and automatic
reviews from any AI provider. Reviews without findings, failed reviews, and cancelled
reviews do not trigger posting. Comments are published after commit by the separate
`post_review_comments` task, with retries and duplicate protection. Disabling the
setting also skips queued automatic posting jobs; manual posting remains available.
If the broker is unavailable when posting is queued, the completed review and findings
are retained, the enqueue failure is logged, and comments can be posted manually
without rerunning the AI review. Apply migrations before deploying this feature.

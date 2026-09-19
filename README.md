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
celery -A config beat -l info         # scheduler (separate terminal)
```

## Docker (alternative)

```bash
cp .env.example .env
docker compose -f .docker/dev/docker-compose.yml up --build
```

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
apps/credentials/  encrypted credentials
apps/admin_extensions/  custom admin
```
## OpenCode Reviews

OpenCode reviews run in the Celery worker as a read-only agent against a disposable
checkout of the merge request source branch. The worker image must contain `git`
and the OpenCode CLI. Configure the worker's OpenCode credentials in its dedicated
`~/.local/share/opencode` data directory; do not mount a developer's home directory
into the worker. `OPENCODE_CONFIG_DIR` is optional and controls extra configuration.

Create an OpenCode provider in the admin dashboard with provider type `OpenCode
Agent` and a model in `provider/model` format, for example
`opencode/deepseek-v4.1-flash`. The selected project must have a clone URL and a
configured Git source credential. Enable `auto_review_enabled` only after testing
the generated reviews manually.

The bundled `.opencode/agents/review.md` agent denies edits, shell commands, web
access, and subagents. Review output is expected to be JSON and is validated by the
same parser used by the HTTP providers. Review comments are still published only by
the separate `post_review_comments` task.

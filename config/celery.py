"""Celery application for the merge_pipeline project."""

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("merge_pipeline")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.conf.beat_schedule = {
    "sync-all-active-projects": {"task": "apps.sync.tasks.sync_all_projects", "schedule": crontab(minute="*/15")},
    "sync-stale-projects": {"task": "apps.sync.tasks.sync_stale_projects", "schedule": crontab(minute=0, hour="*/6")},
    "cleanup-old-webhook-events": {"task": "apps.sync.tasks.cleanup_webhook_events", "schedule": crontab(minute=0, hour=3)},
    "backfill-merge-request-size-labels": {
        "task": "apps.sizing.tasks.queue_pending_size_labels",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "size-labeling"},
    },
}
app.conf.timezone = "UTC"

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.admin_extensions.queue_status import get_cached_queue_snapshot, get_queue_snapshot
from apps.projects.models import MergeRequest, Project
from apps.sources.models import GitSource


class QueueSnapshotTests(TestCase):
    def setUp(self) -> None:
        """Create project records used to resolve safe task targets."""
        cache.clear()
        source = GitSource.objects.create(
            name="GitHub", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        self.project = Project.objects.create(
            source=source,
            external_id="project-1",
            name="merge-pipeline",
            full_name="zoodex/merge-pipeline",
            web_url="https://github.com/zoodex/merge-pipeline",
        )
        self.merge_request = MergeRequest.objects.create(
            project=self.project,
            external_id="mr-7",
            external_iid="7",
            title="Show queue status",
            state=MergeRequest.OPENED,
            author_username="operator",
            source_branch="queue-status",
            target_branch="main",
            web_url="https://github.com/zoodex/merge-pipeline/pull/7",
            opened_at=timezone.now(),
            external_updated_at=timezone.now(),
        )

    @patch("apps.admin_extensions.queue_status._broker_queue_depth", return_value=3)
    @patch("apps.admin_extensions.queue_status.celery_app")
    def test_snapshot_reports_workers_and_safe_job_details(
        self, celery_app: MagicMock, _queue_depth: MagicMock
    ) -> None:
        """Running and waiting tasks are summarized without exposing arguments."""
        inspector = celery_app.control.inspect.return_value
        inspector.active.return_value = {
            "celery@worker-1": [
                {
                    "name": "apps.reviews.tasks.run_manual_ai_review",
                    "args": [self.merge_request.pk],
                    "time_start": datetime.now(tz=UTC).timestamp() - 12,
                    "kwargs": {"secret": "must-not-leak"},
                }
            ]
        }
        inspector.reserved.return_value = {
            "celery@worker-1": [
                {
                    "name": "apps.sync.tasks.sync_project",
                    "args": [self.project.pk],
                    "kwargs": {},
                }
            ]
        }
        inspector.scheduled.return_value = {"celery@worker-1": []}

        snapshot = get_queue_snapshot()

        self.assertEqual(snapshot["health"], "online")
        self.assertEqual(snapshot["counts"], {"workers": 1, "running": 1, "waiting": 4, "scheduled": 0})
        self.assertEqual(snapshot["jobs"][0]["task"], "Manual AI review")
        self.assertEqual(snapshot["jobs"][0]["target"], "zoodex/merge-pipeline !7")
        self.assertEqual(snapshot["jobs"][1]["target"], "zoodex/merge-pipeline")
        self.assertNotIn("args", snapshot["jobs"][0])
        self.assertNotIn("kwargs", snapshot["jobs"][0])
        self.assertNotIn("must-not-leak", str(snapshot))

    @patch("apps.admin_extensions.queue_status._broker_queue_depth", side_effect=ConnectionError)
    @patch("apps.admin_extensions.queue_status.celery_app")
    def test_snapshot_is_unavailable_when_broker_and_workers_are_offline(
        self, celery_app: MagicMock, _queue_depth: MagicMock
    ) -> None:
        """Infrastructure outages produce an empty unavailable snapshot."""
        inspector = celery_app.control.inspect.return_value
        inspector.active.return_value = None
        inspector.reserved.return_value = None
        inspector.scheduled.return_value = None

        snapshot = get_queue_snapshot()

        self.assertEqual(snapshot["health"], "unavailable")
        self.assertEqual(snapshot["counts"], {"workers": 0, "running": 0, "waiting": 0, "scheduled": 0})
        self.assertEqual(snapshot["jobs"], [])

    @patch("apps.admin_extensions.queue_status.get_queue_snapshot")
    def test_cached_snapshot_coalesces_repeated_dashboard_polls(self, collect: MagicMock) -> None:
        """Tabs polling together reuse one recent Celery inspection result."""
        collect.return_value = {"health": "online"}

        first = get_cached_queue_snapshot()
        second = get_cached_queue_snapshot()

        self.assertEqual(first, {"health": "online"})
        self.assertEqual(second, first)
        collect.assert_called_once_with()


class QueueStatusEndpointTests(TestCase):
    def setUp(self) -> None:
        """Create a staff user authorized to read operational data."""
        self.user = get_user_model().objects.create_user(username="operator", password="test-password")
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])

    def test_anonymous_users_cannot_read_queue_status(self) -> None:
        """Queue details remain behind the existing staff boundary."""
        response = self.client.get(reverse("admin_extensions:queue_status"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.headers["Location"])

    @patch("apps.admin_extensions.views.get_cached_queue_snapshot")
    def test_staff_users_receive_queue_snapshot(self, snapshot: MagicMock) -> None:
        """The endpoint returns a fresh queue snapshot without browser caching."""
        snapshot.return_value = {
            "health": "online",
            "counts": {"workers": 1, "running": 0, "waiting": 0, "scheduled": 0},
            "jobs": [],
            "updated_at": "2026-10-01T12:00:00+00:00",
        }
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin_extensions:queue_status"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), snapshot.return_value)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_dashboard_contains_live_queue_panel(self) -> None:
        """The overview exposes the panel and its polling endpoint."""
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin_extensions:dashboard"))

        self.assertContains(response, "Queue status")
        self.assertContains(response, reverse("admin_extensions:queue_status"))
        self.assertContains(response, "AbortController")

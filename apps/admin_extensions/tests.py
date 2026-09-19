from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.credentials.models import GitSourceCredential
from apps.projects.models import MergeRequest, Project
from apps.reviews.models import AIProvider
from apps.sources.models import GitSource


class DashboardAccessTests(TestCase):
    """Verify the separate panel uses Django's existing staff boundary."""

    def test_anonymous_users_are_sent_to_django_admin_login(self) -> None:
        response = self.client.get(reverse("admin_extensions:dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('admin:login')}?next={reverse('admin_extensions:dashboard')}",
        )

    def test_staff_users_can_open_dashboard(self) -> None:
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)

        response = self.client.get(reverse("admin_extensions:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review operations")
        self.assertContains(response, "Active projects")

    def test_panel_pages_are_available_to_staff_users(self) -> None:
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)

        for page_name, heading in (
            ("projects", "Projects"),
            ("merge_requests", "Merge requests"),
            ("reviews", "AI reviews"),
        ):
            with self.subTest(page_name=page_name):
                response = self.client.get(reverse(f"admin_extensions:{page_name}"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, heading)

    def test_merge_request_index_shows_manual_review_action(self) -> None:
        """The full merge-request index exposes the manual review action."""
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)
        source = GitSource.objects.create(
            name="index-source", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        project = Project.objects.create(
            source=source,
            external_id="2",
            name="api",
            full_name="org/api",
            web_url="https://github.com/org/api",
        )
        merge_request = MergeRequest.objects.create(
            project=project,
            external_id="11",
            external_iid="11",
            title="Fix",
            state=MergeRequest.OPENED,
            author_username="dev",
            source_branch="fix",
            target_branch="main",
            web_url="https://github.com/org/api/pull/11",
            opened_at=timezone.now(),
            external_updated_at=timezone.now(),
        )
        response = self.client.get(reverse("admin_extensions:merge_requests"))
        self.assertContains(response, "Run AI review")
        self.assertContains(
            response,
            reverse("admin_extensions:run_merge_request_review", args=[merge_request.pk]),
        )
        self.assertContains(response, "View details")
        self.assertContains(
            response,
            reverse("admin_extensions:merge_request_detail", args=[merge_request.pk]),
        )

    def test_merge_request_detail_renders_request_context(self) -> None:
        """Staff users can inspect a request without leaving the operations panel."""
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)
        source = GitSource.objects.create(
            name="detail-source", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        project = Project.objects.create(
            source=source,
            external_id="3",
            name="web",
            full_name="org/web",
            web_url="https://github.com/org/web",
        )
        merge_request = MergeRequest.objects.create(
            project=project,
            external_id="12",
            external_iid="12",
            title="Improve dashboard",
            description="A useful description.",
            state=MergeRequest.OPENED,
            author_username="maintainer",
            source_branch="feature/dashboard",
            target_branch="main",
            web_url="https://github.com/org/web/pull/12",
            opened_at=timezone.now(),
            external_updated_at=timezone.now(),
        )

        response = self.client.get(
            reverse("admin_extensions:merge_request_detail", args=[merge_request.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Improve dashboard")
        self.assertContains(response, "feature/dashboard")
        self.assertContains(response, "No AI review yet")
        self.assertContains(response, reverse("admin_extensions:merge_request_detail", args=[merge_request.pk]))

    def test_source_management_stays_inside_panel_and_encrypts_token(self) -> None:
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin_extensions:source_create"),
            {
                "name": "Company GitLab",
                "source_type": GitSource.GITLAB,
                "base_url": "https://gitlab.example.com/api/v4",
                "access_token": "secret-token",
                "private_key": "",
                "webhook_secret": "webhook-secret",
                "is_active": "on",
                "sync_enabled": "on",
            },
        )

        self.assertRedirects(response, reverse("admin_extensions:sources"))
        credential = GitSourceCredential.objects.get(source__name="Company GitLab")
        self.assertNotEqual(credential.access_token_encrypted, b"secret-token")

    def test_provider_management_stays_inside_panel_and_encrypts_key(self) -> None:
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)

        response = self.client.post(
            reverse("admin_extensions:provider_create"),
            {
                "name": "Review model",
                "provider_type": AIProvider.OPENAI,
                "api_base_url": "https://api.openai.com/v1",
                "model_name": "gpt-4o-mini",
                "api_key": "secret-api-key",
                "max_tokens_per_request": "8000",
                "temperature": "0.1",
                "input_cost_per_million": "0",
                "output_cost_per_million": "0",
                "is_active": "on",
                "is_default": "on",
                "system_prompt": "",
                "review_prompt_template": "",
            },
        )

        self.assertRedirects(response, reverse("admin_extensions:providers"))
        provider = AIProvider.objects.get(name="Review model")
        self.assertNotEqual(provider.api_key_encrypted, b"secret-api-key")

    def test_dashboard_can_queue_manual_review_for_one_merge_request(self) -> None:
        """A dashboard POST queues only the selected merge request."""
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)
        source = GitSource.objects.create(
            name="review-source", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        project = Project.objects.create(
            source=source,
            external_id="1",
            name="api",
            full_name="org/api",
            web_url="https://github.com/org/api",
            clone_url="https://github.com/org/api.git",
        )
        merge_request = MergeRequest.objects.create(
            project=project,
            external_id="10",
            external_iid="10",
            title="Fix",
            state=MergeRequest.OPENED,
            author_username="dev",
            source_branch="fix",
            target_branch="main",
            web_url="https://github.com/org/api/pull/10",
            opened_at=timezone.now(),
            external_updated_at=timezone.now(),
        )

        with self.subTest("dashboard action is visible"):
            response = self.client.get(reverse("admin_extensions:dashboard"))
            self.assertContains(response, "Run AI review")
            self.assertContains(
                response, reverse("admin_extensions:run_merge_request_review", args=[merge_request.pk])
            )

        with self.subTest("dashboard action queues review"):
            with patch(
                "apps.admin_extensions.views.ReviewQueueService.queue_manual_review", return_value="task-id"
            ):
                response = self.client.post(
                    reverse("admin_extensions:run_merge_request_review", args=[merge_request.pk])
                )
            self.assertRedirects(response, reverse("admin_extensions:dashboard"))
            messages = list(response.wsgi_request._messages)
            self.assertTrue(any("AI review queued" in str(message) for message in messages))

    def test_dashboard_review_endpoint_rejects_get(self) -> None:
        """Review execution is never triggered by a GET request."""
        response = self.client.get(reverse("admin_extensions:run_merge_request_review", args=[1]))
        self.assertEqual(response.status_code, 302)

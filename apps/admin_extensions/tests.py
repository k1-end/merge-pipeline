from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.credentials.models import GitSourceCredential
from apps.projects.models import MergeRequest, Project
from apps.reviews.models import AIProvider, Review, ReviewComment
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
        self.assertContains(response, "Sync merge requests")

    def test_dashboard_can_queue_merge_request_sync(self) -> None:
        """The dashboard exposes a staff-only POST action for source synchronization."""
        user = get_user_model().objects.create_user(username="sync-operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)

        with patch("apps.sync.tasks.sync_all_projects.delay") as sync_delay:
            response = self.client.post(reverse("admin_extensions:sync_merge_requests"))

        self.assertRedirects(response, reverse("admin_extensions:dashboard"))
        sync_delay.assert_called_once_with()
        messages = list(response.wsgi_request._messages)
        self.assertTrue(any("Merge-request sync queued" in str(message) for message in messages))

    def test_dashboard_sync_endpoint_rejects_get(self) -> None:
        """Synchronization is never triggered by a GET request."""
        user = get_user_model().objects.create_user(username="sync-reader", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)

        response = self.client.get(reverse("admin_extensions:sync_merge_requests"))

        self.assertEqual(response.status_code, 405)

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

    def test_review_detail_renders_execution_context_and_findings(self) -> None:
        """A staff user can inspect one review and its persisted comments."""
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)
        source = GitSource.objects.create(
            name="review-detail-source", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        project = Project.objects.create(
            source=source,
            external_id="review-detail-project",
            name="web",
            full_name="org/web",
            web_url="https://github.com/org/web",
        )
        merge_request = MergeRequest.objects.create(
            project=project,
            external_id="21",
            external_iid="21",
            title="Improve review display",
            state=MergeRequest.OPENED,
            author_username="maintainer",
            source_branch="feature/reviews",
            target_branch="main",
            web_url="https://github.com/org/web/pull/21",
            opened_at=timezone.now(),
            external_updated_at=timezone.now(),
        )
        provider = AIProvider.objects.create(
            name="Review detail model",
            provider_type=AIProvider.OPENAI,
            model_name="gpt-4o-mini",
        )
        review = Review.objects.create(
            merge_request=merge_request,
            provider=provider,
            status=Review.COMPLETED,
            summary="The change is focused and ready for review.",
            findings=[{"severity": "low", "message": "Consider adding a regression test."}],
            input_tokens=120,
            output_tokens=80,
            cost_usd="0.004200",
            duration_ms=842,
        )
        ReviewComment.objects.create(
            review=review,
            file_path="templates/reviews.html",
            line_number=18,
            comment_type="suggestion",
            severity="low",
            content="Consider adding a regression test.",
        )

        response = self.client.get(reverse("admin_extensions:review_detail", args=[review.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review summary")
        self.assertContains(response, "The change is focused and ready for review.")
        self.assertContains(response, "templates/reviews.html")
        self.assertContains(response, "Review detail model")
        self.assertContains(response, reverse("admin_extensions:merge_request_detail", args=[merge_request.pk]))

    def test_reviews_index_links_to_review_detail(self) -> None:
        """Each review row exposes its dedicated show page."""
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)
        source = GitSource.objects.create(
            name="review-index-source", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        project = Project.objects.create(
            source=source,
            external_id="review-index-project",
            name="api",
            full_name="org/api",
            web_url="https://github.com/org/api",
        )
        merge_request = MergeRequest.objects.create(
            project=project,
            external_id="22",
            external_iid="22",
            title="Fix API response",
            state=MergeRequest.OPENED,
            author_username="dev",
            source_branch="fix/api",
            target_branch="main",
            web_url="https://github.com/org/api/pull/22",
            opened_at=timezone.now(),
            external_updated_at=timezone.now(),
        )
        provider = AIProvider.objects.create(
            name="Index model",
            provider_type=AIProvider.OPENCODE,
            model_name="reviewer",
        )
        review = Review.objects.create(merge_request=merge_request, provider=provider)

        response = self.client.get(reverse("admin_extensions:reviews"))

        self.assertContains(response, reverse("admin_extensions:review_detail", args=[review.pk]))

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

    def test_merge_request_index_filters_by_project(self) -> None:
        """The project selector limits rows while retaining the selected option."""
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)
        source = GitSource.objects.create(
            name="filter-source", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        selected_project = Project.objects.create(
            source=source,
            external_id="filter-selected",
            name="web",
            full_name="org/web",
            web_url="https://github.com/org/web",
        )
        other_project = Project.objects.create(
            source=source,
            external_id="filter-other",
            name="api",
            full_name="org/api",
            web_url="https://github.com/org/api",
        )
        for project, external_id, title in (
            (selected_project, "31", "Selected request"),
            (other_project, "32", "Other request"),
        ):
            MergeRequest.objects.create(
                project=project,
                external_id=external_id,
                external_iid=external_id,
                title=title,
                state=MergeRequest.OPENED,
                author_username="dev",
                source_branch="feature",
                target_branch="main",
                web_url=f"https://github.com/{project.full_name}/pull/{external_id}",
                opened_at=timezone.now(),
                external_updated_at=timezone.now(),
            )

        response = self.client.get(
            reverse("admin_extensions:merge_requests"), {"project": selected_project.pk}
        )

        self.assertContains(response, "Selected request")
        self.assertNotContains(response, "Other request")
        self.assertContains(response, f'value="{selected_project.pk}" selected')
        self.assertContains(response, "choices.js@11.1.0/public/assets/styles/choices.min.css")
        self.assertNotContains(response, "public/assets/styles/base.min.css")
        self.assertContains(response, "choices.min.js")

    def test_merge_request_index_ignores_invalid_project_filter(self) -> None:
        """An unknown project value returns an empty result instead of an error."""
        user = get_user_model().objects.create_user(username="operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)

        response = self.client.get(reverse("admin_extensions:merge_requests"), {"project": "unknown"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No merge requests have been imported yet.")

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

    def test_projects_page_can_queue_repository_cache_clone(self) -> None:
        """Staff can request repository caching from the projects web interface."""
        user = get_user_model().objects.create_user(username="cache-operator", password="test-password")
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(user)
        source = GitSource.objects.create(
            name="cache-source", source_type=GitSource.GITHUB, base_url="https://api.github.com"
        )
        project = Project.objects.create(
            source=source,
            external_id="cache-project",
            name="cached-api",
            full_name="org/cached-api",
            web_url="https://github.com/org/cached-api",
            clone_url="https://github.com/org/cached-api.git",
        )

        response = self.client.get(reverse("admin_extensions:projects"))
        self.assertContains(response, "Clone cache")
        self.assertContains(response, reverse("admin_extensions:clone_project_cache", args=[project.pk]))

        with patch("apps.reviews.tasks.clone_repository_cache.delay") as clone_delay:
            response = self.client.post(reverse("admin_extensions:clone_project_cache", args=[project.pk]))

        self.assertRedirects(response, reverse("admin_extensions:projects"))
        clone_delay.assert_called_once_with(project.pk)
        project.refresh_from_db()
        self.assertEqual(project.cache_status, Project.CACHE_CLONING)

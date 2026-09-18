from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.credentials.models import GitSourceCredential
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

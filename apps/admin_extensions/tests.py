from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


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

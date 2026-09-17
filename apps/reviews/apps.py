"""AI review pipeline, providers, and comments."""

from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reviews"
    verbose_name = "AI Reviews"

    def ready(self) -> None:
        """Load review deployment checks."""
        import apps.reviews.checks  # noqa: F401

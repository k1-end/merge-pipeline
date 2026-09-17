"""Git source (host) adaptors and webhook handling."""

from django.apps import AppConfig


class SourcesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.sources"
    verbose_name = "Sources & Webhooks"

    def ready(self) -> None:
        """Register built-in source adaptors after Django app loading."""
        from apps.sources.factory import register_builtin_adaptors

        register_builtin_adaptors()

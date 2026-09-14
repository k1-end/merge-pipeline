"""Custom Django Admin registrations and widgets."""

from django.apps import AppConfig


class AdminExtensionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.admin_extensions"
    verbose_name = "Admin Extensions"

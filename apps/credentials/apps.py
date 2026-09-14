"""Encrypted credential storage and encryption service."""

from django.apps import AppConfig


class CredentialsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.credentials"
    verbose_name = "Credentials"

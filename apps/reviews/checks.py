"""Django deployment checks for review configuration."""

from typing import Any

from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def review_settings_check(app_configs: Any = None, **kwargs: Any) -> list[Any]:
    """Flag unsafe review settings before deployment."""
    errors: list[Any] = []
    if not settings.CREDENTIAL_ENCRYPTION_KEY and not settings.TESTING:
        errors.append(Error("Credential encryption is not configured.", id="reviews.E001"))
    if settings.MAX_REVIEW_DIFF_BYTES <= 0:
        errors.append(Error("MAX_REVIEW_DIFF_BYTES must be positive.", id="reviews.E002"))
    if settings.AI_PROVIDER_TIMEOUT_SECONDS <= 0:
        errors.append(Warning("AI provider timeout is not positive.", id="reviews.W001"))
    return errors

"""Webhook endpoint routes.

Phase 3 registers per-source webhook receivers here:
    - GitLab:  /webhooks/gitlab/<source_id>/
    - GitHub:  /webhooks/github/<source_id>/
"""

from django.urls import path

from apps.sources.views import GitHubWebhookView, GitLabWebhookView

app_name = "sources"

urlpatterns = [
    path("gitlab/<int:source_id>/", GitLabWebhookView.as_view(), name="gitlab-webhook"),
    path("github/<int:source_id>/", GitHubWebhookView.as_view(), name="github-webhook"),
]

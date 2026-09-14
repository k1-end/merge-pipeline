"""Webhook endpoint routes.

Phase 3 registers per-source webhook receivers here:
    - GitLab:  /webhooks/gitlab/<source_id>/
    - GitHub:  /webhooks/github/<source_id>/
"""

app_name = "sources"

urlpatterns: list = []

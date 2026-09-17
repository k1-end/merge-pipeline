"""Provider webhook receivers."""

import json

from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from apps.sources.factory import get_adaptor
from apps.sources.models import GitSource
from apps.sync.models import WebhookEvent
from apps.sync.tasks import process_webhook_event


@method_decorator(csrf_exempt, name="dispatch")
class SourceWebhookView(View):
    """Receive and queue a signed source webhook."""

    source_type: str
    signature_header: str

    def post(self, request: HttpRequest, source_id: int) -> JsonResponse:
        """Validate the signature, persist the payload, and queue processing."""
        if len(request.body) > 2 * 1024 * 1024:
            return JsonResponse({"error": "Payload too large"}, status=413)
        source = get_object_or_404(GitSource, id=source_id, source_type=self.source_type, is_active=True)
        try:
            payload = json.loads(request.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        signature = request.headers.get(self.signature_header)
        if not get_adaptor(source).verify_webhook(request.body, signature):
            return JsonResponse({"error": "Invalid signature"}, status=401)
        event = WebhookEvent.objects.create(
            source=source,
            event_type=request.headers.get("X-GitHub-Event") or request.headers.get("X-Gitlab-Event") or "unknown",
            payload=payload,
            headers={key: value for key, value in request.headers.items() if key.lower() not in {"authorization", "cookie"}},
        )
        process_webhook_event.delay(event.id)
        return JsonResponse({"status": "accepted", "event_id": event.id}, status=202)


class GitLabWebhookView(SourceWebhookView):
    """Receive GitLab webhook events."""

    source_type = GitSource.GITLAB
    signature_header = "X-Gitlab-Token"


class GitHubWebhookView(SourceWebhookView):
    """Receive GitHub webhook events."""

    source_type = GitSource.GITHUB
    signature_header = "X-Hub-Signature-256"

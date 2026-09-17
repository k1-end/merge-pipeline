import hashlib
import hmac
import json
from typing import Any
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.sources.models import GitSource
from apps.sync.models import WebhookEvent


@pytest.fixture
def source(settings: Any, db: Any) -> GitSource:
    """Create a signed GitHub source."""
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    source = GitSource.objects.create(
        name="test-github", source_type=GitSource.GITHUB, base_url="https://api.github.com", webhook_secret="hook-secret"
    )
    GitSourceCredential.objects.create(source=source, access_token_encrypted=CredentialEncryption().encrypt("token"))
    return source


def test_github_webhook_requires_valid_signature(client: Any, source: GitSource, monkeypatch: Any) -> None:
    """Invalid webhook signatures are rejected before persistence."""
    payload = {"action": "opened"}
    body = json.dumps(payload).encode()
    monkeypatch.setattr("apps.sources.views.get_adaptor", lambda source: Mock(verify_webhook=lambda body, signature: False))

    response = client.post(f"/webhooks/github/{source.id}/", data=body, content_type="application/json")

    assert response.status_code == 401
    assert not WebhookEvent.objects.exists()


def test_github_webhook_persists_and_queues_signed_event(client: Any, source: GitSource, monkeypatch: Any) -> None:
    """A valid webhook is stored and queued for asynchronous processing."""
    payload = {"action": "opened"}
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
    task = Mock()
    monkeypatch.setattr("apps.sources.views.process_webhook_event", task)

    response = client.post(
        f"/webhooks/github/{source.id}/", data=body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature, HTTP_X_GITHUB_EVENT="pull_request",
    )

    assert response.status_code == 202
    assert WebhookEvent.objects.get().event_type == "pull_request"
    task.delay.assert_called_once()

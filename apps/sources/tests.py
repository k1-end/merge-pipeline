from typing import Any

import pytest
from cryptography.fernet import Fernet

from apps.credentials.models import GitSourceCredential
from apps.credentials.services import CredentialEncryption
from apps.sources.exceptions import AdaptorError
from apps.sources.factory import get_adaptor
from apps.sources.models import GitSource


@pytest.fixture
def source(db: Any) -> GitSource:
    return GitSource.objects.create(
        name="company-gitlab", source_type=GitSource.GITLAB, base_url="https://git.example/api/v4"
    )


@pytest.fixture
def credential(settings: Any, source: GitSource) -> GitSourceCredential:
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    return GitSourceCredential.objects.create(
        source=source,
        access_token_encrypted=CredentialEncryption().encrypt("test-token"),
    )


def test_source_string_and_defaults(source: GitSource) -> None:
    assert str(source) == "company-gitlab"
    assert source.is_active is True
    assert source.sync_enabled is True


def test_factory_builds_registered_adaptor(source: GitSource, credential: GitSourceCredential) -> None:
    assert get_adaptor(source).__class__.__name__ == "GitLabAdaptor"


def test_factory_requires_credentials(source: GitSource) -> None:
    with pytest.raises(AdaptorError, match="no credentials"):
        get_adaptor(source)


def test_factory_rejects_unknown_source_type(
    source: GitSource, credential: GitSourceCredential
) -> None:
    source.source_type = "unknown"
    source.save(update_fields=["source_type"])
    with pytest.raises(AdaptorError, match="No adaptor registered"):
        get_adaptor(source)

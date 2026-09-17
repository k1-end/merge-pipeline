from typing import Any

import pytest
from cryptography.fernet import Fernet

from apps.credentials.services import CredentialEncryption, CredentialError


def test_credential_encryption_round_trip(settings: Any) -> None:
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()
    cipher = CredentialEncryption()

    encrypted = cipher.encrypt("secret-token")

    assert encrypted != b"secret-token"
    assert cipher.decrypt(encrypted) == "secret-token"


def test_credential_encryption_rejects_invalid_key() -> None:
    with pytest.raises(CredentialError, match="valid Fernet key"):
        CredentialEncryption("invalid-key")


def test_credential_encryption_rejects_empty_secret(settings: Any) -> None:
    settings.CREDENTIAL_ENCRYPTION_KEY = Fernet.generate_key().decode()

    with pytest.raises(CredentialError, match="cannot be empty"):
        CredentialEncryption().encrypt("")

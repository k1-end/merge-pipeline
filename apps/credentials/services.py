"""Credential encryption and access helpers."""


from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class CredentialError(ValueError):
    """Raised when credential encryption configuration or data is invalid."""


class CredentialEncryption:
    """Encrypt secrets with a Fernet key configured outside the application."""

    def __init__(self, key: str | None = None):
        """Initialize encryption with an explicit or configured Fernet key."""
        raw_key = (key if key is not None else settings.CREDENTIAL_ENCRYPTION_KEY).encode()
        try:
            self.cipher = Fernet(raw_key)
        except (ValueError, TypeError) as exc:
            raise CredentialError("CREDENTIAL_ENCRYPTION_KEY must be a valid Fernet key") from exc

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a non-empty plaintext secret."""
        if not plaintext:
            raise CredentialError("Credential cannot be empty")
        return self.cipher.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt ciphertext or raise a safe credential error."""
        try:
            return self.cipher.decrypt(bytes(ciphertext)).decode()
        except (InvalidToken, UnicodeDecodeError, TypeError) as exc:
            raise CredentialError("Credential could not be decrypted") from exc


def generate_key() -> str:
    """Return a Fernet key suitable for CREDENTIAL_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode()

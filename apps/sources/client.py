"""Small HTTP client shared by source adaptors."""

import requests

from apps.credentials.services import CredentialEncryption
from apps.sources.exceptions import AdaptorConnectionError


class SourceClient:
    """Authenticated HTTP client with bounded request timeouts."""

    def __init__(self, base_url: str, token_encrypted: bytes, timeout: float = 10.0):
        """Create a client with a bounded timeout and decrypted in-memory token."""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {CredentialEncryption().decrypt(token_encrypted)}"}
        )

    def get(self, path: str) -> requests.Response:
        """GET a provider endpoint and normalize request failures."""
        try:
            response = self.session.get(f"{self.base_url}/{path.lstrip('/')}", timeout=self.timeout)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            raise AdaptorConnectionError("Remote source connection failed") from exc

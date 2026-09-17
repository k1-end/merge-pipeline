"""Common contract implemented by GitLab and GitHub adaptors."""

from abc import ABC, abstractmethod
from typing import Any

from apps.credentials.models import GitSourceCredential
from apps.sources.models import GitSource


class BaseGitAdaptor(ABC):
    """Base interface for source-specific API adaptors."""

    def __init__(self, source: GitSource, credential: GitSourceCredential):
        """Initialize an adaptor with its source and encrypted credentials."""
        self.source = source
        self.credential = credential
        self.client = self._create_client()

    @abstractmethod
    def _create_client(self) -> Any:
        """Create the provider API client."""

    @abstractmethod
    def test_connection(self) -> bool:
        """Return whether the configured source and credential are usable."""

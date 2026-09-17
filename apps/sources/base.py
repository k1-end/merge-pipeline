"""Common contract implemented by GitLab and GitHub adaptors."""

from abc import ABC, abstractmethod
from datetime import datetime
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

    @abstractmethod
    def fetch_projects(self) -> list[dict]:
        """Return normalized repositories visible to the credential."""

    @abstractmethod
    def fetch_merge_requests(
        self, project: Any, updated_after: datetime | None = None
    ) -> list[dict]:
        """Return normalized merge requests for a repository."""

    @abstractmethod
    def fetch_mr_changes(self, project: Any, mr_iid: str) -> list[dict]:
        """Return normalized changed files for a merge request."""

    @abstractmethod
    def parse_webhook(self, payload: dict, event_type: str) -> dict:
        """Normalize a provider webhook payload."""

    @abstractmethod
    def verify_webhook(self, payload: bytes, signature: str | None) -> bool:
        """Verify a provider webhook signature."""

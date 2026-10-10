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
    def fetch_merge_requests(self, project: Any, updated_after: datetime | None = None) -> list[dict]:
        """Return normalized merge requests for a repository."""

    @abstractmethod
    def fetch_open_merge_request_iids(self, project: Any) -> set[str]:
        """Return the provider identifiers for currently open merge requests."""

    @abstractmethod
    def fetch_merge_request(self, project: Any, mr_iid: str) -> dict:
        """Return one normalized merge request by provider identifier."""

    @abstractmethod
    def fetch_mr_changes(self, project: Any, mr_iid: str) -> list[dict]:
        """Return normalized changed files for a merge request."""

    @abstractmethod
    def fetch_review_snapshot(self, project: Any, mr_iid: str) -> dict:
        """Return a consistent revision and changed-file snapshot for inline reviews."""

    @abstractmethod
    def fetch_mr_size(self, project: Any, mr_iid: str) -> tuple[int, int]:
        """Return additions and deletions for a merge request."""

    @abstractmethod
    def fetch_mr_comments(self, project: Any, mr_iid: str) -> list[dict]:
        """Return normalized remote comments and threaded notes."""

    @abstractmethod
    def fetch_mr_reviews(self, project: Any, mr_iid: str) -> list[dict]:
        """Return normalized provider review summaries."""

    @abstractmethod
    def parse_webhook(self, payload: dict, event_type: str) -> dict:
        """Normalize a provider webhook payload."""

    @abstractmethod
    def verify_webhook(self, payload: bytes, signature: str | None) -> bool:
        """Verify a provider webhook signature."""

    @abstractmethod
    def create_comment(self, project: Any, mr_iid: str, position: dict, revision: dict, content: str) -> str:
        """Create an inline review comment and return its note identifier."""

    @abstractmethod
    def find_comment_by_marker(self, project: Any, mr_iid: str, marker: str) -> str | None:
        """Find a previously created review comment by its idempotency marker."""

    @abstractmethod
    def update_mr_state(self, project: Any, mr_iid: str, action: str) -> bool:
        """Apply close, reopen, or merge to a remote merge request."""

    @abstractmethod
    def replace_size_label(self, project: Any, mr_iid: str, label: str) -> None:
        """Replace managed size labels while preserving unrelated labels."""

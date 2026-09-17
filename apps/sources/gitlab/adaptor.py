"""GitLab API adaptor."""

from apps.sources.base import BaseGitAdaptor
from apps.sources.client import SourceClient


class GitLabAdaptor(BaseGitAdaptor):
    """Adaptor for GitLab API v4, including self-hosted instances."""

    def _create_client(self) -> SourceClient:
        return SourceClient(self.source.base_url, self.credential.access_token_encrypted)

    def test_connection(self) -> bool:
        """Verify the token against GitLab's current-user endpoint."""
        self.client.get("user")
        return True

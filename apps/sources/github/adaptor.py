"""GitHub API adaptor."""

import hashlib
import hmac
from typing import Any

from apps.sources.base import BaseGitAdaptor
from apps.sources.client import SourceClient
from apps.sources.exceptions import AdaptorConfigurationError
from apps.sources.normalization import provider_datetime


class GitHubAdaptor(BaseGitAdaptor):
    """Adaptor for the GitHub REST API."""

    def _create_client(self) -> SourceClient:
        return SourceClient(self.source.base_url, self.credential.access_token_encrypted)

    def test_connection(self) -> bool:
        """Verify the token against GitHub's current-user endpoint."""
        self.client.get("user")
        return True

    def fetch_projects(self) -> list[dict]:
        """Fetch repositories visible to the GitHub token."""
        data = self.client.get_json("user/repos", params={"per_page": 100, "sort": "updated"})
        return [
            {
                "external_id": str(item["id"]),
                "name": item["name"],
                "full_name": item["full_name"],
                "web_url": item["html_url"],
                "clone_url": item.get("clone_url") or "",
                "default_branch": item.get("default_branch") or "main",
            }
            for item in data
        ]

    def fetch_merge_requests(self, project: Any, updated_after: Any = None) -> list[dict]:
        """Fetch open GitHub pull requests for a repository."""
        if project.full_name.count("/") != 1 or any(part == "" for part in project.full_name.split("/")):
            raise AdaptorConfigurationError(
                "GitHub project full_name must use the owner/repository format"
            )
        results = []
        page = 1
        while True:
            data = self.client.get_json(
                f"repos/{project.full_name}/pulls",
                params={
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                    "page": page,
                },
            )
            for item in data:
                updated = provider_datetime(item["updated_at"])
                if updated_after and updated <= updated_after:
                    continue
                results.append(
                    {
                        "external_id": str(item["id"]),
                        "external_iid": str(item["number"]),
                        "title": item["title"],
                        "description": item.get("body") or "",
                        "state": "draft" if item.get("draft") else "opened",
                        "author_username": (item.get("user") or {}).get("login", "unknown"),
                        "author_external_id": str((item.get("user") or {}).get("id", "")),
                        "source_branch": item["head"]["ref"],
                        "target_branch": item["base"]["ref"],
                        "web_url": item["html_url"],
                        "opened_at": provider_datetime(item["created_at"]),
                        "external_updated_at": updated,
                        "merged_at": None,
                        "closed_at": None,
                    }
                )
            if len(data) < 100:
                return results
            page += 1

    def fetch_mr_changes(self, project: Any, mr_iid: str) -> list[dict]:
        """Fetch changed files and patches for a GitHub pull request."""
        data = self.client.get_json(
            f"repos/{project.full_name}/pulls/{mr_iid}/files", params={"per_page": 100}
        )
        return [
            {
                "file_path": item["filename"],
                "old_path": "",
                "change_type": {"added": "added", "removed": "deleted", "modified": "modified"}.get(
                    item.get("status"), "modified"
                ),
                "diff": item.get("patch") or "",
            }
            for item in data
        ]

    def parse_webhook(self, payload: dict, event_type: str) -> dict:
        """Normalize a GitHub pull request webhook."""
        pull = payload.get("pull_request", {})
        repo = payload.get("repository", {})
        return {
            "action": payload.get("action", "unknown"),
            "external_project_id": str(repo.get("id", "")),
            "external_iid": str(pull.get("number", "")),
            "external_updated_at": provider_datetime(pull["updated_at"]) if pull.get("updated_at") else None,
        }

    def verify_webhook(self, payload: bytes, signature: str | None) -> bool:
        """Verify GitHub's HMAC-SHA256 signature."""
        if not signature or not self.source.webhook_secret:
            return False
        expected = (
            "sha256=" + hmac.new(self.source.webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
        )
        return hmac.compare_digest(signature, expected)

    def create_comment(
        self, project: Any, mr_iid: str, file_path: str, line: int | None, content: str
    ) -> str:
        """Create a GitHub pull request issue comment."""
        data = self.client.request_json(
            "POST", f"repos/{project.full_name}/issues/{mr_iid}/comments", {"body": content}
        )
        return str(data["id"])

    def update_mr_state(self, project: Any, mr_iid: str, action: str) -> bool:
        """Apply close or reopen to GitHub; merge uses the merge endpoint."""
        if action == "merge":
            self.client.request_json("PUT", f"repos/{project.full_name}/pulls/{mr_iid}/merge", {})
        elif action in {"close", "reopen"}:
            self.client.request_json(
                "PATCH",
                f"repos/{project.full_name}/issues/{mr_iid}",
                {"state": "closed" if action == "close" else "open"},
            )
        else:
            return False
        return True

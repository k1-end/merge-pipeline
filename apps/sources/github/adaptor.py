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
        """Fetch GitHub pull requests, optionally restricted to a time window."""
        if project.full_name.count("/") != 1 or any(part == "" for part in project.full_name.split("/")):
            raise AdaptorConfigurationError("GitHub project full_name must use the owner/repository format")
        results = []
        page = 1
        while True:
            data = self.client.get_json(
                f"repos/{project.full_name}/pulls",
                params={
                    "state": "all" if updated_after else "open",
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
                results.append(self._normalize_merge_request(item))
            if updated_after and data and provider_datetime(data[-1]["updated_at"]) <= updated_after:
                return results
            if len(data) < 100:
                return results
            page += 1

    def fetch_open_merge_request_iids(self, project: Any) -> set[str]:
        """Fetch all currently open GitHub pull-request numbers."""
        return {
            item["external_iid"]
            for item in self.fetch_merge_requests(project)
            if item["state"] in {"opened", "draft"}
        }

    def fetch_merge_request(self, project: Any, mr_iid: str) -> dict:
        """Fetch and normalize one GitHub pull request."""
        data = self.client.get_json(f"repos/{project.full_name}/pulls/{mr_iid}")
        return self._normalize_merge_request(data)

    @staticmethod
    def _normalize_merge_request(item: dict) -> dict:
        """Convert a GitHub pull request into the local merge-request shape."""
        updated = provider_datetime(item["updated_at"])
        merged_at = provider_datetime(item["merged_at"]) if item.get("merged_at") else None
        closed_at = provider_datetime(item["closed_at"]) if item.get("closed_at") else None
        state = "merged" if merged_at else "closed" if closed_at else "opened"
        return {
            "external_id": str(item["id"]),
            "external_iid": str(item["number"]),
            "title": item["title"],
            "description": item.get("body") or "",
            "state": "draft" if item.get("draft") and state == "opened" else state,
            "author_username": (item.get("user") or {}).get("login", "unknown"),
            "author_external_id": str((item.get("user") or {}).get("id", "")),
            "source_branch": item["head"]["ref"],
            "source_sha": item["head"].get("sha") or "",
            "target_branch": item["base"]["ref"],
            "web_url": item["html_url"],
            "opened_at": provider_datetime(item["created_at"]),
            "external_updated_at": updated,
            "merged_at": merged_at,
            "closed_at": closed_at,
        }

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

    def _fetch_pages(self, path: str) -> list[dict]:
        """Fetch every page from a GitHub list endpoint."""
        results: list[dict] = []
        page = 1
        while True:
            data = self.client.get_json(path, params={"per_page": 100, "page": page})
            if not isinstance(data, list):
                raise AdaptorConfigurationError("GitHub returned an invalid paginated response")
            results.extend(data)
            if len(data) < 100:
                return results
            page += 1

    def fetch_mr_comments(self, project: Any, mr_iid: str) -> list[dict]:
        """Fetch top-level and inline GitHub pull-request comments."""
        comments = []
        for item in self._fetch_pages(f"repos/{project.full_name}/issues/{mr_iid}/comments"):
            user = item.get("user") or {}
            comments.append(
                {
                    "provider_kind": "github_issue_comment",
                    "category": "general",
                    "external_id": str(item["id"]),
                    "external_thread_id": "",
                    "external_review_id": "",
                    "parent_external_id": "",
                    "author_username": user.get("login") or "unknown",
                    "author_external_id": str(user.get("id") or ""),
                    "body": item.get("body") or "",
                    "web_url": item.get("html_url") or "",
                    "file_path": "",
                    "line_number": None,
                    "start_line_number": None,
                    "line_side": "",
                    "commit_sha": "",
                    "is_resolvable": False,
                    "is_resolved": False,
                    "provider_created_at": provider_datetime(item["created_at"]),
                    "provider_updated_at": provider_datetime(item["updated_at"]),
                }
            )
        for item in self._fetch_pages(f"repos/{project.full_name}/pulls/{mr_iid}/comments"):
            user = item.get("user") or {}
            comments.append(
                {
                    "provider_kind": "github_review_comment",
                    "category": "inline",
                    "external_id": str(item["id"]),
                    "external_thread_id": str(item.get("in_reply_to_id") or item["id"]),
                    "external_review_id": str(item.get("pull_request_review_id") or ""),
                    "parent_external_id": str(item.get("in_reply_to_id") or ""),
                    "author_username": user.get("login") or "unknown",
                    "author_external_id": str(user.get("id") or ""),
                    "body": item.get("body") or "",
                    "web_url": item.get("html_url") or "",
                    "file_path": item.get("path") or "",
                    "line_number": item.get("line") or item.get("original_line"),
                    "start_line_number": item.get("start_line") or item.get("original_start_line"),
                    "line_side": (item.get("side") or item.get("original_side") or "").lower(),
                    "commit_sha": item.get("commit_id") or "",
                    "is_resolvable": False,
                    "is_resolved": False,
                    "provider_created_at": provider_datetime(item["created_at"]),
                    "provider_updated_at": provider_datetime(item["updated_at"]),
                }
            )
        return comments

    def fetch_mr_reviews(self, project: Any, mr_iid: str) -> list[dict]:
        """Fetch GitHub pull-request review summaries and decisions."""
        reviews = []
        for item in self._fetch_pages(f"repos/{project.full_name}/pulls/{mr_iid}/reviews"):
            user = item.get("user") or {}
            submitted_at = provider_datetime(item["submitted_at"]) if item.get("submitted_at") else None
            reviews.append(
                {
                    "external_id": str(item["id"]),
                    "author_username": user.get("login") or "unknown",
                    "author_external_id": str(user.get("id") or ""),
                    "body": item.get("body") or "",
                    "state": (item.get("state") or "commented").lower(),
                    "commit_sha": item.get("commit_id") or "",
                    "web_url": item.get("html_url") or "",
                    "provider_created_at": submitted_at,
                    "provider_updated_at": submitted_at,
                }
            )
        return reviews

    def parse_webhook(self, payload: dict, event_type: str) -> dict:
        """Normalize a GitHub pull request or discussion webhook."""
        pull = payload.get("pull_request", {})
        if event_type == "issue_comment":
            issue = payload.get("issue", {})
            pull = issue if issue.get("pull_request") else {}
        elif event_type == "pull_request_review":
            pull = payload.get("pull_request", {})
        elif event_type == "pull_request_review_comment":
            pull = payload.get("pull_request", {})
        repo = payload.get("repository", {})
        return {
            "action": payload.get("action", "unknown"),
            "external_project_id": str(repo.get("id", "")),
            "external_iid": str(pull.get("number", "")),
            "external_updated_at": provider_datetime(pull["updated_at"]) if pull.get("updated_at") else None,
            "sync_discussion": event_type
            in {"issue_comment", "pull_request_review", "pull_request_review_comment"}
            and bool(pull),
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

    def find_comment_by_marker(self, project: Any, mr_iid: str, marker: str) -> str | None:
        """Find a GitHub pull-request issue comment containing the marker."""
        page = 1
        while True:
            comments = self.client.get_json(
                f"repos/{project.full_name}/issues/{mr_iid}/comments",
                params={"per_page": 100, "page": page},
            )
            for comment in comments:
                if marker in (comment.get("body") or ""):
                    return str(comment["id"])
            if len(comments) < 100:
                return None
            page += 1

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

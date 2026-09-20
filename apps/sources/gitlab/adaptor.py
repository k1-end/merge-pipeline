"""GitLab API adaptor."""

import hmac
from typing import Any

from apps.sources.base import BaseGitAdaptor
from apps.sources.client import SourceClient
from apps.sources.normalization import provider_datetime


class GitLabAdaptor(BaseGitAdaptor):
    """Adaptor for GitLab API v4, including self-hosted instances."""

    def _create_client(self) -> SourceClient:
        return SourceClient(self.source.base_url, self.credential.access_token_encrypted)

    def test_connection(self) -> bool:
        """Verify the token against GitLab's current-user endpoint."""
        self.client.get("user")
        return True

    def fetch_projects(self) -> list[dict]:
        """Fetch repositories accessible to the GitLab token."""
        data = self.client.get_json("projects", params={"membership": "true", "per_page": 100})
        return [
            {
                "external_id": str(item["id"]),
                "name": item["name"],
                "full_name": item["path_with_namespace"],
                "web_url": item["web_url"],
                "clone_url": item.get("http_url_to_repo") or "",
                "default_branch": item.get("default_branch") or "main",
            }
            for item in data
        ]

    def fetch_merge_requests(self, project: Any, updated_after: Any = None) -> list[dict]:
        """Fetch GitLab merge requests, optionally restricted to a time window."""
        params = {
            "state": "all" if updated_after else "opened",
            "order_by": "updated_at",
            "sort": "desc",
            "per_page": 100,
            "page": 1,
        }
        if updated_after:
            params["updated_after"] = updated_after.isoformat()
        results = []
        while True:
            data = self.client.get_json(f"projects/{project.external_id}/merge_requests", params=params)
            results.extend(self._normalize_merge_request(item) for item in data)
            if len(data) < params["per_page"]:
                return results
            params["page"] += 1

    def fetch_open_merge_request_iids(self, project: Any) -> set[str]:
        """Fetch all currently open GitLab merge-request IIDs."""
        return {item["external_iid"] for item in self.fetch_merge_requests(project) if item["state"] in {"opened", "draft"}}

    def fetch_merge_request(self, project: Any, mr_iid: str) -> dict:
        """Fetch and normalize one GitLab merge request."""
        item = self.client.get_json(f"projects/{project.external_id}/merge_requests/{mr_iid}")
        return self._normalize_merge_request(item)

    @staticmethod
    def _normalize_merge_request(item: dict) -> dict:
        """Convert a GitLab merge request into the local merge-request shape."""
        merged_at = provider_datetime(item["merged_at"]) if item.get("merged_at") else None
        closed_at = provider_datetime(item["closed_at"]) if item.get("closed_at") else None
        state = "merged" if merged_at else item["state"]
        return {
            "external_id": str(item["id"]),
            "external_iid": str(item["iid"]),
            "title": item["title"],
            "description": item.get("description") or "",
            "state": "draft" if item.get("draft") and state == "opened" else state,
            "author_username": (item.get("author") or {}).get("username", "unknown"),
            "author_external_id": str((item.get("author") or {}).get("id", "")),
            "source_branch": item["source_branch"],
            "target_branch": item["target_branch"],
            "web_url": item["web_url"],
            "opened_at": provider_datetime(item["created_at"]),
            "external_updated_at": provider_datetime(item["updated_at"]),
            "merged_at": merged_at,
            "closed_at": closed_at,
        }

    def fetch_mr_changes(self, project: Any, mr_iid: str) -> list[dict]:
        """Fetch changed files and diffs for a GitLab merge request."""
        data = self.client.get_json(f"projects/{project.external_id}/merge_requests/{mr_iid}/changes")
        return [
            {
                "file_path": item["new_path"],
                "old_path": item.get("old_path") or "",
                "change_type": (
                    "renamed"
                    if item.get("renamed_file")
                    else (
                        "added"
                        if item.get("new_file")
                        else "deleted" if item.get("deleted_file") else "modified"
                    )
                ),
                "diff": item.get("diff") or "",
            }
            for item in data.get("changes", [])
        ]

    def parse_webhook(self, payload: dict, event_type: str) -> dict:
        """Normalize a GitLab merge request webhook."""
        attrs = payload.get("object_attributes", {})
        project = payload.get("project", {})
        return {
            "action": attrs.get("action") or payload.get("event_type", "unknown"),
            "external_project_id": str(project.get("id", "")),
            "external_iid": str(attrs.get("iid", "")),
            "external_updated_at": (
                provider_datetime(attrs["updated_at"]) if attrs.get("updated_at") else None
            ),
        }

    def verify_webhook(self, payload: bytes, signature: str | None) -> bool:
        """Compare GitLab's token header without timing leaks."""
        return bool(signature) and hmac.compare_digest(signature or "", self.source.webhook_secret)

    def create_comment(
        self, project: Any, mr_iid: str, file_path: str, line: int | None, content: str
    ) -> str:
        """Create a GitLab MR note."""
        data = self.client.request_json(
            "POST", f"projects/{project.external_id}/merge_requests/{mr_iid}/notes", {"body": content}
        )
        return str(data["id"])

    def find_comment_by_marker(self, project: Any, mr_iid: str, marker: str) -> str | None:
        """Find a GitLab merge-request note containing the marker."""
        page = 1
        while True:
            notes = self.client.get_json(
                f"projects/{project.external_id}/merge_requests/{mr_iid}/notes",
                params={"per_page": 100, "page": page},
            )
            for note in notes:
                if marker in (note.get("body") or ""):
                    return str(note["id"])
            if len(notes) < 100:
                return None
            page += 1

    def update_mr_state(self, project: Any, mr_iid: str, action: str) -> bool:
        """Apply a supported GitLab MR state transition."""
        state = {"close": "close", "reopen": "reopen", "merge": "merge"}.get(action)
        if state is None:
            return False
        self.client.request_json("PUT", f"projects/{project.external_id}/merge_requests/{mr_iid}/{state}")
        return True

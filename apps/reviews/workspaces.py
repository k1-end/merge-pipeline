"""Disposable worktrees created from persistent repository caches."""

import re
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from uuid import uuid4

from apps.projects.models import Project
from apps.reviews.repository_cache import RepositoryCache, RepositoryCacheError


class WorkspaceError(RuntimeError):
    """Raised when a review repository cannot be prepared safely."""


REVIEW_AGENT = """---
description: Reviews merge request changes without modifying files.
mode: primary
permissions:
  - action: "*"
    resource: "*"
    effect: deny
  - action: read
    resource: "*"
    effect: allow
  - action: glob
    resource: "*"
    effect: allow
  - action: grep
    resource: "*"
    effect: allow
  - action: shell
    resource: "git diff *"
    effect: allow
  - action: shell
    resource: "git status *"
    effect: allow
  - action: shell
    resource: "git show *"
    effect: allow
  - action: shell
    resource: "git log *"
    effect: allow
---

Review the specified base...head comparison and inspect surrounding repository code when needed.
Do not modify files, commit changes, push code, access secrets, or inspect directories outside the repository.
Return only the requested JSON review result. Use repository-relative file paths and valid changed-file line numbers.
"""


class ReviewWorkspace(AbstractContextManager[str]):
    """Create a disposable worktree from a project's persistent bare cache."""

    def __init__(
        self,
        project: Project,
        source_branch: str,
        target_branch: str,
        *,
        base_sha: str,
        head_sha: str,
        merge_base_sha: str = "",
    ):
        """Pin a workspace to immutable snapshot commits; branches are metadata only."""
        self.project = project
        self.source_branch = source_branch
        self.target_branch = target_branch
        self.base_sha = base_sha
        self.head_sha = head_sha
        self.merge_base_sha = merge_base_sha
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._workspace: Path | None = None
        self._cache = RepositoryCache(project)
        self._base_ref = ""
        self._head_ref = ""
        self._merge_base_ref = ""

    def __enter__(self) -> str:
        """Retrieve exact snapshot commits and check out its head without recloning."""
        try:
            for sha in (self.base_sha, self.head_sha, self.merge_base_sha):
                if sha and not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
                    raise WorkspaceError("Review snapshot requires full commit SHAs")
            if not self.base_sha or not self.head_sha:
                raise WorkspaceError("Review snapshot requires full commit SHAs")
            self._cache.ensure_exists()
            self._temporary = tempfile.TemporaryDirectory(prefix="merge-review-")
            self._workspace = Path(self._temporary.name) / "checkout"
            namespace = f"refs/merge-pipeline/workspaces/{uuid4().hex}"
            self._base_ref = f"{namespace}/base"
            self._head_ref = f"{namespace}/head"
            self._merge_base_ref = f"{namespace}/merge-base"
            with self._cache.git_environment() as environment, self._cache.locked():
                self._pin_commit(self.base_sha, self._base_ref, environment)
                self._pin_commit(self.head_sha, self._head_ref, environment)
                if self.merge_base_sha:
                    self._pin_commit(self.merge_base_sha, self._merge_base_ref, environment)
                computed_merge_base = self._cache.run(
                    [
                        "git",
                        "--git-dir",
                        str(self._cache.path),
                        "merge-base",
                        self.base_sha,
                        self.head_sha,
                    ]
                ).stdout.strip()
                if self.merge_base_sha and computed_merge_base != self.merge_base_sha:
                    raise WorkspaceError("Review snapshot merge base does not match its pinned commits")
                self.merge_base_sha = computed_merge_base
                self._cache.run(
                    [
                        "git",
                        "--git-dir",
                        str(self._cache.path),
                        "worktree",
                        "add",
                        "--detach",
                        str(self._workspace),
                        self._head_ref,
                    ]
                )
            agent_path = self._workspace / ".opencode" / "agents" / "review.md"
            agent_path.parent.mkdir(parents=True, exist_ok=True)
            agent_path.write_text(REVIEW_AGENT, encoding="utf-8")
            return str(self._workspace)
        except (RepositoryCacheError, WorkspaceError):
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise WorkspaceError("Could not prepare review workspace") from exc

    def _resolve_ref(self, ref: str) -> str:
        """Resolve a fetched review ref to its immutable commit SHA."""
        return self._cache.run(
            ["git", "--git-dir", str(self._cache.path), "rev-parse", f"{ref}^{{commit}}"]
        ).stdout.strip()

    def _pin_commit(self, sha: str, ref: str, environment: dict[str, str]) -> None:
        """Protect cached objects, or fetch the exact SHA without falling back to branch tips."""
        git = ["git", "--git-dir", str(self._cache.path)]
        try:
            self._cache.run([*git, "cat-file", "-e", f"{sha}^{{commit}}"])
        except RepositoryCacheError:
            try:
                self._cache.run([*git, "fetch", "--no-tags", "origin", f"+{sha}:{ref}"], environment)
            except RepositoryCacheError as exc:
                raise WorkspaceError(
                    f"Could not retrieve review snapshot commit {sha}; run a fresh review or restore remote access"
                ) from exc
        self._cache.run([*git, "update-ref", ref, sha])
        if self._resolve_ref(ref) != sha:
            raise WorkspaceError("Review snapshot commit did not resolve to its exact SHA")

    def close(self) -> None:
        """Remove the disposable worktree while retaining the bare cache."""
        if (
            (self._workspace and self._workspace.exists())
            or self._base_ref
            or self._head_ref
            or self._merge_base_ref
        ):
            try:
                with self._cache.locked():
                    if self._workspace and self._workspace.exists():
                        self._cache.run(
                            [
                                "git",
                                "--git-dir",
                                str(self._cache.path),
                                "worktree",
                                "remove",
                                "--force",
                                str(self._workspace),
                            ]
                        )
                    self._cache.run(["git", "--git-dir", str(self._cache.path), "worktree", "prune"])
                    for ref in (self._base_ref, self._head_ref, self._merge_base_ref):
                        if ref:
                            self._cache.run(
                                ["git", "--git-dir", str(self._cache.path), "update-ref", "-d", ref]
                            )
            except RepositoryCacheError:
                pass
            self._workspace = None
            self._base_ref = ""
            self._head_ref = ""
            self._merge_base_ref = ""
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Clean up the workspace after the review."""
        self.close()

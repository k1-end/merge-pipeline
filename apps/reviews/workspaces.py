"""Disposable worktrees created from persistent repository caches."""

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

    def __init__(self, project: Project, source_branch: str, target_branch: str):
        """Initialize a workspace for the requested source and target branches."""
        self.project = project
        self.source_branch = source_branch
        self.target_branch = target_branch
        self.base_sha = ""
        self.head_sha = ""
        self.merge_base_sha = ""
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._workspace: Path | None = None
        self._cache = RepositoryCache(project)
        self._base_ref = ""
        self._head_ref = ""

    def __enter__(self) -> str:
        """Fetch and check out the source branch without cloning the repository."""
        try:
            self._cache.ensure_exists()
            self._cache.run(["git", "check-ref-format", "--branch", self.source_branch])
            self._cache.run(["git", "check-ref-format", "--branch", self.target_branch])
            self._temporary = tempfile.TemporaryDirectory(prefix="merge-review-")
            self._workspace = Path(self._temporary.name) / "checkout"
            namespace = f"refs/merge-pipeline/workspaces/{uuid4().hex}"
            self._base_ref = f"{namespace}/base"
            self._head_ref = f"{namespace}/head"
            base_refspec = f"+refs/heads/{self.target_branch}:{self._base_ref}"
            head_refspec = f"+refs/heads/{self.source_branch}:{self._head_ref}"
            with self._cache.git_environment() as environment, self._cache.locked():
                self._cache.run(
                    [
                        "git",
                        "--git-dir",
                        str(self._cache.path),
                        "fetch",
                        "origin",
                        base_refspec,
                        head_refspec,
                    ],
                    environment,
                )
                self.base_sha = self._resolve_ref(self._base_ref)
                self.head_sha = self._resolve_ref(self._head_ref)
                self.merge_base_sha = self._cache.run(
                    [
                        "git",
                        "--git-dir",
                        str(self._cache.path),
                        "merge-base",
                        self.base_sha,
                        self.head_sha,
                    ]
                ).stdout.strip()
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
        except RepositoryCacheError:
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

    def close(self) -> None:
        """Remove the disposable worktree while retaining the bare cache."""
        if (self._workspace and self._workspace.exists()) or self._base_ref or self._head_ref:
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
                    for ref in (self._base_ref, self._head_ref):
                        if ref:
                            self._cache.run(
                                ["git", "--git-dir", str(self._cache.path), "update-ref", "-d", ref]
                            )
            except RepositoryCacheError:
                pass
            self._workspace = None
            self._base_ref = ""
            self._head_ref = ""
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Clean up the workspace after the review."""
        self.close()

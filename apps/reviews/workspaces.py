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
permission:
  read: allow
  glob: allow
  grep: allow
  lsp: allow
  edit: deny
  bash: deny
  task: deny
  webfetch: deny
  websearch: deny
---

Review the current merge request changes and inspect surrounding repository code when needed.
Do not modify files, commit changes, push code, access secrets, or inspect directories outside the repository.
Return only the requested JSON review result. Use repository-relative file paths and valid changed-file line numbers.
"""


class ReviewWorkspace(AbstractContextManager[str]):
    """Create a disposable worktree from a project's persistent bare cache."""

    def __init__(self, project: Project, source_branch: str):
        """Initialize a workspace for the requested source branch."""
        self.project = project
        self.source_branch = source_branch
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._workspace: Path | None = None
        self._cache = RepositoryCache(project)

    def __enter__(self) -> str:
        """Fetch and check out the source branch without cloning the repository."""
        try:
            self._cache.ensure_exists()
            self._cache.run(["git", "check-ref-format", "--branch", self.source_branch])
            self._temporary = tempfile.TemporaryDirectory(prefix="merge-review-")
            self._workspace = Path(self._temporary.name) / "checkout"
            review_ref = f"refs/merge-pipeline/workspaces/{uuid4().hex}"
            refspec = f"+refs/heads/{self.source_branch}:{review_ref}"
            with self._cache.git_environment() as environment, self._cache.locked():
                self._cache.run(
                    ["git", "--git-dir", str(self._cache.path), "fetch", "origin", refspec],
                    environment,
                )
                self._cache.run(
                    [
                        "git",
                        "--git-dir",
                        str(self._cache.path),
                        "worktree",
                        "add",
                        "--detach",
                        str(self._workspace),
                        review_ref,
                    ]
                )
                self._cache.run(["git", "--git-dir", str(self._cache.path), "update-ref", "-d", review_ref])
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

    def close(self) -> None:
        """Remove the disposable worktree while retaining the bare cache."""
        if self._workspace and self._workspace.exists():
            try:
                with self._cache.locked():
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
            except RepositoryCacheError:
                pass
            self._workspace = None
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Clean up the workspace after the review."""
        self.close()

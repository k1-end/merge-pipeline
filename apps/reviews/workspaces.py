"""Disposable repository workspaces for agent-based reviews."""

import os
import stat
import subprocess
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from django.core.exceptions import ObjectDoesNotExist

from apps.credentials.services import CredentialEncryption
from apps.projects.models import Project


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
    """Clone a merge request source branch into a disposable directory."""

    def __init__(self, project: Project, source_branch: str):
        """Initialize a workspace for the requested source branch."""
        self.project = project
        self.source_branch = source_branch
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._askpass: Path | None = None

    def __enter__(self) -> str:
        """Clone and check out the source branch."""
        if not self.project.clone_url:
            raise WorkspaceError("Project has no clone URL")
        try:
            credential = self.project.source.credential
        except ObjectDoesNotExist:
            credential = None
        if credential is None or not credential.access_token_encrypted:
            raise WorkspaceError("Project has no Git credential")

        self._temporary = tempfile.TemporaryDirectory(prefix="merge-review-")
        workspace = Path(self._temporary.name)
        token = CredentialEncryption().decrypt(credential.access_token_encrypted)
        self._askpass = workspace.parent / f"{workspace.name}-askpass"
        self._askpass.write_text(
            '#!/bin/sh\ncase "$1" in\n*Username*) printf \'%s\\n\' "$GIT_REVIEW_USERNAME" ;;\n*) printf \'%s\\n\' "$GIT_REVIEW_TOKEN" ;;\nesac\n',
            encoding="utf-8",
        )
        self._askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_ASKPASS": str(self._askpass),
                "GIT_REVIEW_USERNAME": (
                    "oauth2" if self.project.source.source_type == "gitlab" else "x-access-token"
                ),
                "GIT_REVIEW_TOKEN": token,
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        try:
            self._run(["git", "clone", "--no-checkout", self.project.clone_url, str(workspace)], environment)
            self._run(["git", "-C", str(workspace), "fetch", "origin", self.source_branch], environment)
            self._run(["git", "-C", str(workspace), "checkout", "--detach", "FETCH_HEAD"], environment)
            agent_path = workspace / ".opencode" / "agents" / "review.md"
            agent_path.parent.mkdir(parents=True, exist_ok=True)
            agent_path.write_text(REVIEW_AGENT, encoding="utf-8")
            return str(workspace)
        except Exception:
            self.close()
            raise

    def _run(self, command: list[str], environment: dict[str, str]) -> None:
        try:
            subprocess.run(  # noqa: S603 - command is a fixed git argv list, never shell-evaluated
                command, env=environment, check=True, capture_output=True, text=True, timeout=120
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspaceError("Could not prepare review workspace") from exc

    def close(self) -> None:
        """Delete the temporary credential helper and repository."""
        if self._askpass:
            self._askpass.unlink(missing_ok=True)
            self._askpass = None
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Clean up the workspace after the review."""
        self.close()

"""Persistent bare repository caches used to create disposable review worktrees."""

import fcntl
import os
import shutil
import stat
import subprocess  # nosec B404
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

from apps.credentials.services import CredentialEncryption, CredentialError
from apps.projects.models import Project


class RepositoryCacheError(RuntimeError):
    """Raised when a repository cache operation fails."""


class RepositoryCacheConfigurationError(RepositoryCacheError):
    """Raised when a project cannot authenticate or has no clone URL."""


class RepositoryCacheMissingError(RepositoryCacheError):
    """Raised when a review is requested before its cache has been cloned."""


class RepositoryCache:
    """Manage one project's bare Git cache inside Docker-managed storage."""

    def __init__(self, project: Project):
        """Resolve deterministic cache and lock paths for a project."""
        self.project = project
        self.root = Path(settings.REPOSITORY_CACHE_ROOT)
        self.path = self.root / f"project-{project.pk}.git"
        self.lock_path = self.root / f".project-{project.pk}.lock"

    def exists(self, validate: bool = True) -> bool:
        """Return whether a usable bare repository exists for the project."""
        if not self.path.is_dir():
            return False
        if not validate:
            return True
        try:
            clone_url = self._clone_url()
            result = self.run(["git", "--git-dir", str(self.path), "rev-parse", "--is-bare-repository"])
            origin = self.run(["git", "--git-dir", str(self.path), "remote", "get-url", "origin"])
        except RepositoryCacheError:
            return False
        return result.stdout.strip() == "true" and origin.stdout.strip() == clone_url

    def ensure_exists(self) -> None:
        """Reject reviews that have not been explicitly cached from the web UI."""
        if not self.exists():
            raise RepositoryCacheMissingError(
                "Repository cache is missing. Clone it from the Projects page before running a review."
            )

    def clone(self) -> None:
        """Create the bare cache atomically, or leave an existing valid cache unchanged."""
        clone_url = self._clone_url()
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if self.exists():
                return
            if self.path.exists():
                shutil.rmtree(self.path)
            temporary_path = self.root / f".{self.path.name}-{uuid4().hex}.tmp"
            try:
                with self.git_environment() as environment:
                    self.run(
                        ["git", "clone", "--bare", clone_url, str(temporary_path)],
                        environment,
                    )
                self.run(
                    [
                        "git",
                        "--git-dir",
                        str(temporary_path),
                        "config",
                        "remote.origin.fetch",
                        "+refs/heads/*:refs/remotes/origin/*",
                    ]
                )
                temporary_path.replace(self.path)
                if not self.exists():
                    raise RepositoryCacheError("Repository cache was cloned but could not be verified.")
            finally:
                if temporary_path.exists():
                    shutil.rmtree(temporary_path)

    @contextmanager
    def git_environment(self) -> Iterator[dict[str, str]]:
        """Provide Git credentials through a short-lived askpass helper."""
        token = self._access_token()
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor, askpass_name = tempfile.mkstemp(prefix="git-askpass-", dir=self.root)
        askpass = Path(askpass_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as helper:
                helper.write(
                    '#!/bin/sh\ncase "$1" in\n*Username*) printf \'%s\\n\' "$GIT_REVIEW_USERNAME" ;;\n*) printf \'%s\\n\' "$GIT_REVIEW_TOKEN" ;;\nesac\n'
                )
            askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            environment = os.environ.copy()
            environment.update(
                {
                    "GIT_ASKPASS": str(askpass),
                    "GIT_REVIEW_USERNAME": (
                        "oauth2" if self.project.source.source_type == "gitlab" else "x-access-token"
                    ),
                    "GIT_REVIEW_TOKEN": token,
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
            yield environment
        finally:
            askpass.unlink(missing_ok=True)

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize fetch and worktree metadata changes for this project."""
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            yield

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with self.lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _clone_url(self) -> str:
        if not self.project.clone_url:
            raise RepositoryCacheConfigurationError("Project has no clone URL.")
        parsed_url = urlsplit(self.project.clone_url)
        if parsed_url.username is not None or parsed_url.password is not None:
            raise RepositoryCacheConfigurationError(
                "Clone URL must not contain credentials; configure the Git source credential instead."
            )
        return self.project.clone_url

    def _access_token(self) -> str:
        try:
            credential = self.project.source.credential
        except ObjectDoesNotExist as exc:
            raise RepositoryCacheConfigurationError("Project has no Git credential.") from exc
        if not credential.access_token_encrypted:
            raise RepositoryCacheConfigurationError("Project has no Git credential.")
        try:
            return CredentialEncryption().decrypt(credential.access_token_encrypted)
        except CredentialError as exc:
            raise RepositoryCacheConfigurationError("Project Git credential is invalid.") from exc

    @staticmethod
    def run(
        command: list[str], environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run a bounded Git command without shell evaluation."""
        try:
            return subprocess.run(  # noqa: S603  # nosec B603
                command,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RepositoryCacheError("Repository cache operation failed.") from exc

"""Adaptor factory and built-in registrations."""

from apps.sources.base import BaseGitAdaptor
from apps.sources.models import GitSource
from apps.sources.registry import AdaptorRegistry


def get_adaptor(source: GitSource) -> BaseGitAdaptor:
    """Build the registered adaptor for a configured source."""
    return AdaptorRegistry.get_for_source(source)


def register_builtin_adaptors() -> None:
    """Register built-in providers once Django has loaded the apps."""
    from apps.sources.github.adaptor import GitHubAdaptor
    from apps.sources.gitlab.adaptor import GitLabAdaptor

    AdaptorRegistry.register("github", GitHubAdaptor)
    AdaptorRegistry.register("gitlab", GitLabAdaptor)

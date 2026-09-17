"""Runtime registry for source adaptors."""


from apps.sources.base import BaseGitAdaptor
from apps.sources.exceptions import AdaptorError
from apps.sources.models import GitSource


class AdaptorRegistry:
    """Map source type values to adaptor classes."""

    _adaptors: dict[str, type[BaseGitAdaptor]] = {}

    @classmethod
    def register(cls, source_type: str, adaptor_class: type[BaseGitAdaptor]) -> None:
        """Register or replace an adaptor for a source type."""
        cls._adaptors[source_type] = adaptor_class

    @classmethod
    def get(cls, source_type: str) -> type[BaseGitAdaptor]:
        """Return the adaptor class registered for a source type."""
        try:
            return cls._adaptors[source_type]
        except KeyError as exc:
            raise AdaptorError(f"No adaptor registered for {source_type}") from exc

    @classmethod
    def get_for_source(cls, source: GitSource) -> BaseGitAdaptor:
        """Build an adaptor for a source with its related credentials."""
        credential = getattr(source, "credential", None)
        if credential is None:
            raise AdaptorError(f"Source {source.name} has no credentials")
        return cls.get(source.source_type)(source, credential)

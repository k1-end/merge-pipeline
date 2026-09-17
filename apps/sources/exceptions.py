"""Errors raised by source adaptors."""


class AdaptorError(Exception):
    """Base error for remote source operations."""


class AdaptorConfigurationError(AdaptorError):
    """Raised when a source is missing usable configuration."""


class AdaptorConnectionError(AdaptorError):
    """Raised when a source cannot be reached or rejects credentials."""

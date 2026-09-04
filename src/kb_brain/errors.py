"""Platform-level exceptions.

Every error the platform raises across a contract boundary (API, SDK, MCP)
subclasses ``KBError`` so interface adapters can map them to transport codes
without knowing about internal modules.
"""

from __future__ import annotations


class KBError(Exception):
    """Base class for all Knowledge Brain errors."""

    status_code: int = 500
    code: str = "internal_error"


class ConfigError(KBError):
    """Malformed or missing platform configuration."""

    status_code = 500
    code = "config_error"


class ScopeError(KBError):
    """A scope reference could not be parsed or is structurally invalid."""

    status_code = 400
    code = "invalid_scope"


class PolicyDenied(KBError):
    """The principal is not permitted to perform the operation on the scope."""

    status_code = 403
    code = "policy_denied"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


class SourceUnavailable(KBError):
    """A configured MCP source is not usable (missing credentials, no server)."""

    status_code = 424
    code = "source_unavailable"


class IngestionFailed(KBError):
    """The ingestion run could not be completed."""

    status_code = 502
    code = "ingestion_failed"

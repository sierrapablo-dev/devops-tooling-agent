"""Closed set of error codes (docs/python-package.md, Section 3).

Only the codes that implemented commands emit are defined here. The issues that
introduce the remaining documented codes add them. Every code has a fixed
message and a fixed set of ``details`` keys. Detail values come only from fixed
vocabularies, validated identifiers, numbers, and subprocess exit codes.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

DetailValue = str | int | None

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1
EXIT_USAGE: Final = 2
EXIT_INTERRUPTED: Final = 130


@dataclass(frozen=True)
class _Spec:
    message: str
    details: tuple[str, ...]
    exit_code: int


class ErrorCode(Enum):
    """Error codes, each with a fixed message, detail keys, and exit code."""

    USAGE_ERROR = _Spec("The arguments do not match the command syntax.", ("expected",), EXIT_USAGE)
    INVALID_IDENTIFIER = _Spec(
        "The repository identifier is not accepted.", ("reason",), EXIT_ERROR
    )
    ENVIRONMENT_ERROR = _Spec(
        "A prerequisite is missing or unsupported.",
        ("reason", "tool", "minimum_version"),
        EXIT_ERROR,
    )
    AUTH_REQUIRED = _Spec("gh is not authenticated for github.com.", (), EXIT_ERROR)
    NOT_FOUND_OR_INACCESSIBLE = _Spec(
        "The repository does not exist or is not accessible.", (), EXIT_ERROR
    )
    UNSUPPORTED_REPOSITORY = _Spec(
        "The repository cannot be handled within scope.", ("reason",), EXIT_ERROR
    )
    LIMIT_EXCEEDED = _Spec(
        "A hard limit makes the operation impossible.",
        ("limit", "maximum", "observed"),
        EXIT_ERROR,
    )
    TIMEOUT = _Spec("The internal deadline expired.", ("stage",), EXIT_ERROR)
    PROVIDER_ERROR = _Spec(
        "GitHub, the network, or TLS failed.", ("stage", "exit_code"), EXIT_ERROR
    )
    UNSAFE_ENVIRONMENT = _Spec("The configuration isolation check failed.", ("check",), EXIT_ERROR)
    CLEANUP_FAILED = _Spec(
        "The workspace could not be removed.", ("workspace", "prior_error"), EXIT_ERROR
    )
    WORKSPACE_NOT_ELIGIBLE = _Spec(
        "The workspace named for removal is not eligible.", ("reason",), EXIT_ERROR
    )
    INTERRUPTED = _Spec("The invocation was interrupted by a signal.", ("stage",), EXIT_INTERRUPTED)
    INTERNAL_ERROR = _Spec("An unexpected internal error occurred.", (), EXIT_ERROR)

    @property
    def message(self) -> str:
        return self.value.message

    @property
    def detail_keys(self) -> tuple[str, ...]:
        return self.value.details

    @property
    def exit_code(self) -> int:
        return self.value.exit_code


class CommandError(Exception):
    """An expected failure that is reported as an error envelope."""

    def __init__(self, code: ErrorCode, details: Mapping[str, DetailValue] | None = None) -> None:
        given = dict(details or {})
        if set(given) != set(code.detail_keys):
            raise ValueError("details do not match the documented keys of the error code")
        super().__init__(code.name)
        self.code = code
        # Keep the documented key order.
        self.details: Mapping[str, DetailValue] = MappingProxyType(
            {key: given[key] for key in code.detail_keys}
        )


class Interrupted(BaseException):
    """Raised by the signal handlers. Not an ``Exception``, so it is never swallowed."""

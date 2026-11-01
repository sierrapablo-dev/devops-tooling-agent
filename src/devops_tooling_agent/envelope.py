"""The single JSON envelope written to standard output (ADR-0007 Section 4)."""

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

from devops_tooling_agent import __version__
from devops_tooling_agent.constants import MAX_ENVELOPE_CHARS, MAX_LINE_CHARS
from devops_tooling_agent.errors import CommandError, ErrorCode

SCHEMA: Final = "devops-tooling-agent.envelope"
SCHEMA_VERSION: Final = 1
TOOL_NAME: Final = "devops-tooling-agent"


def _tool() -> dict[str, str]:
    return {"name": TOOL_NAME, "version": __version__}


def success(
    command: str,
    result: Mapping[str, Any],
    *,
    untrusted: Sequence[str] = (),
) -> dict[str, Any]:
    """Build an ``ok`` envelope. Keys follow the documented order."""
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "tool": _tool(),
        "command": command,
        "status": "ok",
        "result": dict(result),
        "error": None,
        "warnings": [],
        "untrusted": list(untrusted),
        "end": True,
    }


def failure(command: str | None, error: CommandError) -> dict[str, Any]:
    """Build an ``error`` envelope. Keys follow the documented order."""
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "tool": _tool(),
        "command": command,
        "status": "error",
        "result": None,
        "error": {
            "code": error.code.name,
            "message": error.code.message,
            "details": dict(error.details),
        },
        "warnings": [],
        "untrusted": [],
        "end": True,
    }


def _serialize_unchecked(envelope: Mapping[str, Any]) -> str:
    # ensure_ascii escapes every non-ASCII character, and indent makes the
    # document line-structured. json.dumps emits "\n" only.
    return json.dumps(envelope, ensure_ascii=True, indent=2, allow_nan=False) + "\n"


def within_bounds(text: str) -> bool:
    """Whether the serialized document respects the line bound and the budget."""
    if len(text) > MAX_ENVELOPE_CHARS:
        return False
    return all(len(line) <= MAX_LINE_CHARS for line in text.split("\n"))


def serialize(envelope: Mapping[str, Any], command: str | None) -> bytes:
    """Serialize an envelope to ASCII bytes with LF line endings.

    An envelope that cannot be serialized within the bounds is a defect. It is
    replaced by an ``INTERNAL_ERROR`` envelope, which always fits.
    """
    try:
        text = _serialize_unchecked(envelope)
    except (TypeError, ValueError):
        text = None
    if text is None or not within_bounds(text):
        text = _serialize_unchecked(failure(command, CommandError(ErrorCode.INTERNAL_ERROR)))
    return text.encode("ascii")

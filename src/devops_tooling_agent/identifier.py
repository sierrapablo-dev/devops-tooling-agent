"""Offline validation of GitHub repository identifiers (ADR-0006 Section 2).

Accepted forms, ASCII only:

- ``https://github.com/<owner>/<repo>`` with an optional ``.git`` suffix;
- ``git@github.com:<owner>/<repo>`` with an optional ``.git`` suffix;
- ``<owner>/<repo>``, where a ``.git`` suffix is part of the repository name.

Every other form is rejected with exactly one reason from a fixed vocabulary.
The rejected value is never reported. The precedence of reasons is documented
in docs/python-package.md, Section 2.1.
"""

import string
from dataclasses import dataclass
from typing import Final, Literal

from devops_tooling_agent.constants import (
    MAX_IDENTIFIER_CHARS,
    MAX_OWNER_CHARS,
    MAX_REPO_NAME_CHARS,
)

Reason = Literal[
    "format",
    "scheme",
    "host",
    "userinfo",
    "port",
    "query",
    "fragment",
    "path",
    "characters",
    "length",
]

HOST: Final = "github.com"
_SCHEME: Final = "https"
_SCP_USER: Final = "git"
_GIT_SUFFIX: Final = ".git"
_OWNER_CHARS: Final = frozenset(string.ascii_letters + string.digits + "-")
_REPO_CHARS: Final = frozenset(string.ascii_letters + string.digits + "._-")
# Printable ASCII without space. Percent-encoding and backslashes are never valid.
_ALLOWED: Final = frozenset(chr(code) for code in range(0x21, 0x7F)) - {"%", "\\"}


@dataclass(frozen=True)
class Repository:
    """A validated ``owner/repo`` pair."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class IdentifierRejected(Exception):
    """The identifier is not accepted. Carries only the reason."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


def owner_problem(owner: str) -> Reason | None:
    """GitHub account name rules: letters, digits, and single inner hyphens."""
    if not owner:
        return "format"
    if not set(owner) <= _OWNER_CHARS:
        return "characters"
    if len(owner) > MAX_OWNER_CHARS:
        return "length"
    if owner.startswith("-") or owner.endswith("-") or "--" in owner:
        return "format"
    return None


def repo_name_problem(name: str) -> Reason | None:
    """Letters, digits, ``.``, ``_``, and ``-``, excluding ``.`` and ``..``."""
    if not name:
        return "format"
    if not set(name) <= _REPO_CHARS:
        return "characters"
    if len(name) > MAX_REPO_NAME_CHARS:
        return "length"
    if name in {".", ".."}:
        return "format"
    return None


def _query_or_fragment(text: str) -> Reason | None:
    for char in text:
        if char == "?":
            return "query"
        if char == "#":
            return "fragment"
    return None


def _two_segments(path: str) -> tuple[str, str]:
    segments = path.split("/")
    if len(segments) == 1:
        raise IdentifierRejected("format")
    if len(segments) != 2:
        raise IdentifierRejected("path")
    return segments[0], segments[1]


def _validated(owner: str, name: str) -> Repository:
    problem = owner_problem(owner) or repo_name_problem(name)
    if problem is not None:
        raise IdentifierRejected(problem)
    return Repository(owner, name)


def _strip_git(name: str) -> str:
    return name[: -len(_GIT_SUFFIX)] if name.endswith(_GIT_SUFFIX) else name


def _parse_url(text: str) -> Repository:
    scheme, rest = text.split("://", 1)
    if scheme != _SCHEME:
        raise IdentifierRejected("scheme")
    end = next((i for i, char in enumerate(rest) if char in "/?#"), len(rest))
    authority, tail = rest[:end], rest[end:]
    if "@" in authority:
        raise IdentifierRejected("userinfo")
    if ":" in authority:
        raise IdentifierRejected("port")
    if authority != HOST:
        raise IdentifierRejected("host")
    problem = _query_or_fragment(tail)
    if problem is not None:
        raise IdentifierRejected(problem)
    if not tail.startswith("/"):
        raise IdentifierRejected("path")
    segments = tail[1:].split("/")
    if len(segments) != 2:
        raise IdentifierRejected("path")
    return _validated(segments[0], _strip_git(segments[1]))


def _parse_scp(text: str) -> Repository:
    user, rest = text.split("@", 1)
    if user != _SCP_USER:
        raise IdentifierRejected("userinfo")
    if ":" not in rest:
        raise IdentifierRejected("format")
    host, path = rest.split(":", 1)
    if "@" in host:
        raise IdentifierRejected("userinfo")
    if host != HOST:
        raise IdentifierRejected("host")
    problem = _query_or_fragment(path)
    if problem is not None:
        raise IdentifierRejected(problem)
    owner, name = _two_segments(path)
    return _validated(owner, _strip_git(name))


def _parse_short(text: str) -> Repository:
    problem = _query_or_fragment(text)
    if problem is not None:
        raise IdentifierRejected(problem)
    if ":" in text:
        raise IdentifierRejected("format")
    owner, name = _two_segments(text)
    return _validated(owner, name)


def parse_identifier(text: str) -> Repository:
    """Validate an identifier and return the normalized ``owner/repo``.

    Raises ``IdentifierRejected`` with a single reason.
    """
    if len(text) > MAX_IDENTIFIER_CHARS:
        raise IdentifierRejected("length")
    if not text:
        raise IdentifierRejected("format")
    if not set(text) <= _ALLOWED:
        raise IdentifierRejected("characters")
    if "://" in text:
        return _parse_url(text)
    if "@" in text:
        return _parse_scp(text)
    return _parse_short(text)


def parse_name_with_owner(text: str) -> Repository | None:
    """Validate a provider-reported ``owner/repo``. Returns ``None`` if invalid."""
    if len(text) > MAX_IDENTIFIER_CHARS or not set(text) <= _ALLOWED:
        return None
    segments = text.split("/")
    if len(segments) != 2:
        return None
    owner, name = segments
    if owner_problem(owner) is not None or repo_name_problem(name) is not None:
        return None
    return Repository(owner, name)

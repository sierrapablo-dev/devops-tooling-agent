"""Serialized bounds of the envelope (ADR-0006 Section 9, ADR-0007 Section 4).

The envelope is serialized with ``json.dumps(..., ensure_ascii=True, indent=2)``.
Every string that a collector emits is fitted to its serialized line before it
is placed in the document: indentation, key, quotes, escapes, and separator
included.
"""

import codecs
import json
from typing import Final

from devops_tooling_agent.constants import MAX_LINE_CHARS

INDENT: Final = 2
LIMIT_LINE: Final = "MAX_LINE_CHARS"

_SHORT_ESCAPES: Final = frozenset('"\\\n\r\t\b\f')


def escaped_length(char: str) -> int:
    """The length of one character inside a JSON string with ``ensure_ascii``."""
    if char in _SHORT_ESCAPES:
        return 2
    code = ord(char)
    if code < 0x20:
        return 6
    if code < 0x80:
        return 1
    return 6 if code <= 0xFFFF else 12


def keyed_overhead(depth: int, key: str) -> int:
    """Characters of a ``"key": "value",`` line other than the escaped value."""
    return INDENT * depth + len(json.dumps(key)) + len(": ") + len('""') + len(",")


def item_overhead(depth: int) -> int:
    """Characters of a ``"value",`` list item line other than the escaped value."""
    return INDENT * depth + len('""') + len(",")


def fit(text: str, overhead: int) -> tuple[str, bool]:
    """The longest prefix of ``text`` whose line fits ``MAX_LINE_CHARS``.

    Returns the prefix and whether ``text`` was truncated. An escaped
    character is never split.
    """
    room = MAX_LINE_CHARS - overhead
    used = 0
    for index, char in enumerate(text):
        used += escaped_length(char)
        if used > room:
            return text[:index], True
    return text, False


def line_chars(text: str, depth: int) -> int:
    """Serialized characters of a list item line, including its line ending."""
    return item_overhead(depth) + sum(escaped_length(char) for char in text) + len("\n")


def decode(raw: bytes, *, final: bool = True) -> tuple[str, bool]:
    """Tolerant UTF-8 decoding. Returns the text and whether replacement occurred.

    With ``final`` false, an incomplete sequence at the end is dropped without
    being reported, because it comes from a byte limit, not from the data.
    """
    try:
        return codecs.getincrementaldecoder("utf-8")("strict").decode(raw, final), False
    except UnicodeDecodeError:
        return codecs.getincrementaldecoder("utf-8")("replace").decode(raw, final), True

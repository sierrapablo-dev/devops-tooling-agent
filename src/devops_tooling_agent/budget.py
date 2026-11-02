"""Serialized bounds of the envelope (ADR-0006 Section 9, ADR-0007 Section 4).

The envelope is serialized with ``json.dumps(..., ensure_ascii=True, indent=2)``.
Every string that a collector emits is fitted to its serialized line before it
is placed in the document: indentation, key, quotes, escapes, and separator
included. When the whole envelope would exceed ``MAX_ENVELOPE_CHARS``, sections
are reduced in a fixed priority order (``reduce``), and every reduction is
recorded in the reduced container and in ``collector.reductions``.
"""

import codecs
import copy
import json
from collections.abc import Callable
from typing import Any, Final

from devops_tooling_agent.constants import MAX_ENVELOPE_CHARS, MAX_LINE_CHARS

INDENT: Final = 2
LIMIT_LINE: Final = "MAX_LINE_CHARS"
LIMIT_ENVELOPE: Final = "MAX_ENVELOPE_CHARS"

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


# -- envelope reduction ---------------------------------------------------------

Measure = Callable[[dict[str, Any]], int]


def _mark(container: dict[str, Any]) -> None:
    container["truncated"] = True
    if LIMIT_ENVELOPE not in container["truncation"]:
        container["truncation"].append(LIMIT_ENVELOPE)


def cut_path_list(original: dict[str, Any], keep: int) -> dict[str, Any]:
    """A path list with only its first ``keep`` paths (and sizes)."""
    cut = copy.deepcopy(original)
    cut["paths"] = cut["paths"][:keep]
    if "sizes" in cut:
        cut["sizes"] = cut["sizes"][:keep]
    cut["listed"] = len(cut["paths"])
    cut["truncated_path_indexes"] = [i for i in cut["truncated_path_indexes"] if i < keep]
    cut["replaced_path_indexes"] = [i for i in cut["replaced_path_indexes"] if i < keep]
    _mark(cut)
    return cut


def cut_dir_list(original: dict[str, Any], keep: int) -> dict[str, Any]:
    cut = copy.deepcopy(original)
    cut["directories"] = cut["directories"][:keep]
    cut["listed"] = len(cut["directories"])
    _mark(cut)
    return cut


def cut_extensions(original: dict[str, Any], keep: int) -> dict[str, Any]:
    cut = copy.deepcopy(original)
    items = list(cut["counts"].items())
    cut["counts"] = dict(items[:keep])
    cut["other"] += sum(count for _, count in items[keep:])
    _mark(cut)
    return cut


def cut_commits(original: dict[str, Any], keep: int) -> dict[str, Any]:
    cut = copy.deepcopy(original)
    cut["recent_commits"] = cut["recent_commits"][:keep]
    cut["returned"] = len(cut["recent_commits"])
    _mark(cut)
    return cut


def cut_contents(original: dict[str, Any], keep: int) -> dict[str, Any]:
    """Keep the first ``keep`` files in selection order; count the others."""
    cut = copy.deepcopy(original)
    removed = len(cut["files"]) - keep
    cut["files"] = cut["files"][:keep]
    cut["included"] = len(cut["files"])
    cut["total_chars"] = sum(file["content_chars"] for file in cut["files"])
    target = cut["not_included"][LIMIT_ENVELOPE]
    target["total"] += removed
    target["truncated"] = target["total"] > target["listed"]
    if target["truncated"] and LIMIT_ENVELOPE not in target["truncation"]:
        target["truncation"].append(LIMIT_ENVELOPE)
    _mark(cut)
    return cut


Cut = Callable[[dict[str, Any], int], dict[str, Any]]

# The fixed priority order of ADR-0006 Section 9: listed paths first. Each
# entry is the reduction name, the path of the container under
# ``result.evidence``, the list that is shortened, and the cut function.
# ``repository``, ``acquisition``, ``collector``, and every count are never
# reduced.
SECTIONS: Final[tuple[tuple[str, tuple[str, ...], str, Cut], ...]] = (
    ("inventory.files", ("inventory", "files"), "paths", cut_path_list),
    ("inventory.symlinks", ("inventory", "symlinks"), "paths", cut_path_list),
    ("inventory.submodules", ("inventory", "submodules"), "paths", cut_path_list),
    (
        "inventory.excluded_directories",
        ("inventory", "excluded_directories"),
        "directories",
        cut_dir_list,
    ),
    (
        "contents.not_included.binary",
        ("contents", "not_included", "binary"),
        "paths",
        cut_path_list,
    ),
    (
        "contents.not_included.MAX_BLOB_BYTES",
        ("contents", "not_included", "MAX_BLOB_BYTES"),
        "paths",
        cut_path_list,
    ),
    (
        "contents.not_included.MAX_CONTENT_FILES",
        ("contents", "not_included", "MAX_CONTENT_FILES"),
        "paths",
        cut_path_list,
    ),
    (
        "contents.not_included.MAX_TOTAL_CONTENT_CHARS",
        ("contents", "not_included", "MAX_TOTAL_CONTENT_CHARS"),
        "paths",
        cut_path_list,
    ),
    ("contents.files", ("contents",), "files", cut_contents),
    ("git.recent_commits", ("git",), "recent_commits", cut_commits),
    ("inventory.extensions", ("inventory", "extensions"), "counts", cut_extensions),
    ("agent_components.files", ("agent_components", "files"), "paths", cut_path_list),
    (
        "agent_components.directories",
        ("agent_components", "directories"),
        "directories",
        cut_dir_list,
    ),
    ("sensitive_files.files", ("sensitive_files", "files"), "paths", cut_path_list),
    (
        "sensitive_files.directories",
        ("sensitive_files", "directories"),
        "directories",
        cut_dir_list,
    ),
)


def _parent(evidence: dict[str, Any], path: tuple[str, ...]) -> tuple[dict[str, Any], str]:
    node = evidence
    for key in path[:-1]:
        node = node[key]
    return node, path[-1]


def _longest_fit(
    result: dict[str, Any],
    parent: dict[str, Any],
    key: str,
    original: dict[str, Any],
    cut: Cut,
    size: int,
    measure: Measure,
) -> bool:
    """Set the longest cut of ``original`` that fits. Returns whether one fits."""
    low, high = 0, size - 1
    best = -1
    while low <= high:
        middle = (low + high) // 2
        parent[key] = cut(original, middle)
        if measure(result) <= MAX_ENVELOPE_CHARS:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    parent[key] = cut(original, max(best, 0))
    return best >= 0


def reduce(result: dict[str, Any], measure: Measure) -> None:
    """Reduce ``result.evidence`` in place until ``measure(result)`` fits.

    Each section keeps the longest prefix of its list that fits, found by
    binary search over the real serialized length, so the outcome is exact and
    deterministic. A section that cannot fit even when empty is emptied, and
    the next one is reduced.
    """
    if measure(result) <= MAX_ENVELOPE_CHARS:
        return
    evidence = result["evidence"]
    collector = result["collector"]
    for name, path, field, cut in SECTIONS:
        parent, key = _parent(evidence, path)
        original = parent[key]
        size = len(original[field])
        if size == 0:
            continue
        collector["reductions"].append(name)
        if LIMIT_ENVELOPE not in collector["limits_reached"]:
            collector["limits_reached"].append(LIMIT_ENVELOPE)
        if _longest_fit(result, parent, key, original, cut, size, measure):
            return

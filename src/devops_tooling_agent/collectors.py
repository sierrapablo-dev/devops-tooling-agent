"""Evidence collectors (ADR-0006 Sections 1, 9, and 11).

Collectors select and bound evidence. They never interpret it, and they never
execute target code. Every string they emit is fitted to its serialized line,
and every limit they reach is recorded in the containing object and in the set
of reached limits that ``collector.limits_reached`` reports. The field-level
schema is documented in docs/python-package.md, Section 2.2.
"""

import re
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from devops_tooling_agent import budget, classify
from devops_tooling_agent.acquire import AcquiredRepository
from devops_tooling_agent.constants import (
    BINARY_SNIFF_BYTES,
    GIT_MAX_OUTPUT_BYTES,
    MAX_BLOB_BYTES,
    MAX_COMMIT_SUBJECT_CHARS,
    MAX_CONTENT_FILES,
    MAX_ENVELOPE_CHARS,
    MAX_EXTENSION_CHARS,
    MAX_EXTENSIONS,
    MAX_FILE_BYTES,
    MAX_FILE_LINES,
    MAX_INVENTORY_PATHS,
    MAX_LINE_CHARS,
    MAX_TOTAL_CONTENT_CHARS,
    MAX_TREE_ENTRIES,
    MAX_TREE_OUTPUT_BYTES,
    RECENT_COMMITS,
)
from devops_tooling_agent.errors import CommandError, ErrorCode
from devops_tooling_agent.tree import OBJECT_ID, Listing, TreeEntry, TreeSource

# The limits reported in collector.limits, in the order of every list of
# limit names in the result.
LIMITS: Final[tuple[tuple[str, int], ...]] = (
    ("RECENT_COMMITS", RECENT_COMMITS),
    ("MAX_TREE_ENTRIES", MAX_TREE_ENTRIES),
    ("MAX_TREE_OUTPUT_BYTES", MAX_TREE_OUTPUT_BYTES),
    ("MAX_INVENTORY_PATHS", MAX_INVENTORY_PATHS),
    ("MAX_EXTENSIONS", MAX_EXTENSIONS),
    ("MAX_EXTENSION_CHARS", MAX_EXTENSION_CHARS),
    ("MAX_CONTENT_FILES", MAX_CONTENT_FILES),
    ("MAX_BLOB_BYTES", MAX_BLOB_BYTES),
    ("BINARY_SNIFF_BYTES", BINARY_SNIFF_BYTES),
    ("MAX_FILE_BYTES", MAX_FILE_BYTES),
    ("MAX_FILE_LINES", MAX_FILE_LINES),
    ("MAX_TOTAL_CONTENT_CHARS", MAX_TOTAL_CONTENT_CHARS),
    ("MAX_COMMIT_SUBJECT_CHARS", MAX_COMMIT_SUBJECT_CHARS),
    ("GIT_MAX_OUTPUT_BYTES", GIT_MAX_OUTPUT_BYTES),
    ("MAX_LINE_CHARS", MAX_LINE_CHARS),
    ("MAX_ENVELOPE_CHARS", MAX_ENVELOPE_CHARS),
)
LIMIT_ORDER: Final = {name: index for index, (name, _) in enumerate(LIMITS)}

# Depth of the keys of each container: the envelope's keys are at depth 1,
# result's at 2, evidence's at 3, and each evidence section's at 4.
SECTION: Final = 4
CONTENT_LINE: Final = 7  # result.evidence.contents.files[i].content[j]
COMMIT_KEY: Final = 6  # result.evidence.git.recent_commits[i].<key>
FILE_KEY: Final = 6  # result.evidence.contents.files[i].<key>

BOM: Final = b"\xef\xbb\xbf"
READ_LIMIT: Final = max(MAX_FILE_BYTES, BINARY_SNIFF_BYTES)
NOT_INCLUDED: Final = (
    "binary",
    "MAX_BLOB_BYTES",
    "MAX_CONTENT_FILES",
    "MAX_TOTAL_CONTENT_CHARS",
    "MAX_ENVELOPE_CHARS",
)
LOG_FORMAT: Final = "--format=%H%x00%cI%x00%s%x00"
LOG_RECORD_END: Final = b"\x00\n"

_EXTENSION: Final = re.compile(rb"\.[a-z0-9_+-]{1,%d}" % MAX_EXTENSION_CHARS)
_DATE: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})",
    re.ASCII,
)


def ordered(names: Iterable[str]) -> list[str]:
    """Limit names without duplicates, in the order of ``LIMITS``."""
    return sorted(set(names), key=lambda name: LIMIT_ORDER.get(name, len(LIMIT_ORDER)))


@dataclass
class Reached:
    """The limits reached anywhere in the evidence."""

    names: set[str] = field(default_factory=set)

    def add(self, *names: str) -> None:
        self.names.update(names)


# -- bounded building blocks ------------------------------------------------------


def _path_text(path: bytes, overhead: int) -> tuple[str, bool, bool]:
    text, replaced = budget.decode(path)
    fitted, truncated = budget.fit(text, overhead)
    return fitted, truncated, replaced


def path_list(
    items: Sequence[tuple[bytes, int | None]],
    *,
    depth: int,
    reached: Reached,
    sizes: bool = False,
    limit: int = MAX_INVENTORY_PATHS,
) -> dict[str, Any]:
    """A path list whose keys are at ``depth``, sorted by the path bytes."""
    items = sorted(items, key=lambda item: item[0])
    listed = items[:limit]
    truncation = ["MAX_INVENTORY_PATHS"] if len(items) > limit else []
    paths: list[str] = []
    truncated_indexes: list[int] = []
    replaced_indexes: list[int] = []
    for index, (path, _) in enumerate(listed):
        text, truncated, replaced = _path_text(path, budget.item_overhead(depth + 1))
        paths.append(text)
        if truncated:
            truncated_indexes.append(index)
        if replaced:
            replaced_indexes.append(index)
    if truncated_indexes:
        reached.add(budget.LIMIT_LINE)
    reached.add(*truncation)
    result: dict[str, Any] = {
        "total": len(items),
        "listed": len(listed),
        "truncated": bool(truncation),
        "truncation": truncation,
        "paths": paths,
    }
    if sizes:
        result["sizes"] = [size for _, size in listed]
    result["truncated_path_indexes"] = truncated_indexes
    result["replaced_path_indexes"] = replaced_indexes
    return result


def dir_list(counts: dict[bytes, int], *, depth: int, reached: Reached) -> dict[str, Any]:
    """A directory list whose keys are at ``depth``: path and entry count only."""
    items = sorted(counts.items())
    listed = items[:MAX_INVENTORY_PATHS]
    truncation = ["MAX_INVENTORY_PATHS"] if len(items) > MAX_INVENTORY_PATHS else []
    reached.add(*truncation)
    directories = []
    for path, entries in listed:
        text, truncated, replaced = _path_text(path, budget.keyed_overhead(depth + 2, "path"))
        if truncated:
            reached.add(budget.LIMIT_LINE)
        directories.append(
            {
                "path": text,
                "path_truncated": truncated,
                "path_replaced": replaced,
                "entries": entries,
            }
        )
    return {
        "total": len(items),
        "listed": len(listed),
        "truncated": bool(truncation),
        "truncation": truncation,
        "directories": directories,
    }


# -- inventory, sensitive files, and agent components ------------------------------


@dataclass
class Survey:
    """Every examined entry, classified once."""

    regular: dict[str, list[TreeEntry]] = field(
        default_factory=lambda: {"file": [], "symlink": [], "submodule": [], "other": []}
    )
    candidates: dict[str, list[TreeEntry]] = field(
        default_factory=lambda: {category: [] for category in classify.CATEGORIES}
    )
    excluded: dict[bytes, int] = field(default_factory=dict)
    sensitive_files: list[bytes] = field(default_factory=list)
    sensitive_directories: dict[bytes, int] = field(default_factory=dict)
    agent_files: list[bytes] = field(default_factory=list)
    agent_directories: dict[bytes, int] = field(default_factory=dict)
    counts: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("sensitive", "agent_components", "excluded"), 0)
    )


def survey(listing: Listing) -> Survey:
    result = Survey()
    for entry in listing.entries:
        found = classify.classify(entry.path)
        if found.entry_class == "sensitive":
            result.counts["sensitive"] += 1
            if found.directory is None:
                result.sensitive_files.append(entry.path)
            else:
                directories = result.sensitive_directories
                directories[found.directory] = directories.get(found.directory, 0) + 1
        elif found.entry_class == "agent":
            result.counts["agent_components"] += 1
            if found.directory is None:
                result.agent_files.append(entry.path)
            else:
                directories = result.agent_directories
                directories[found.directory] = directories.get(found.directory, 0) + 1
        elif found.entry_class == "excluded":
            if found.directory is None:
                raise RuntimeError("an excluded entry always has an excluded directory")
            result.counts["excluded"] += 1
            result.excluded[found.directory] = result.excluded.get(found.directory, 0) + 1
        else:
            result.regular[entry.type].append(entry)
            if entry.type == "file" and found.category is not None:
                result.candidates[found.category].append(entry)
    return result


def extensions(files: Sequence[TreeEntry], reached: Reached) -> dict[str, Any]:
    counts: dict[str, int] = {}
    none = other = 0
    for entry in files:
        name = entry.path.rsplit(b"/", 1)[-1]
        dot = name.rfind(b".")
        if dot <= 0:
            none += 1
            continue
        extension = name[dot:].lower()
        if _EXTENSION.fullmatch(extension):
            key = extension.decode("ascii")
            counts[key] = counts.get(key, 0) + 1
        else:
            other += 1
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0].encode("ascii")))
    truncation = ["MAX_EXTENSIONS"] if len(items) > MAX_EXTENSIONS else []
    reached.add(*truncation)
    other += sum(count for _, count in items[MAX_EXTENSIONS:])
    return {
        "counts": dict(items[:MAX_EXTENSIONS]),
        "none": none,
        "other": other,
        "truncated": bool(truncation),
        "truncation": truncation,
    }


def inventory(listing: Listing, found: Survey, reached: Reached) -> dict[str, Any]:
    if listing.truncation is not None:
        reached.add(listing.truncation)
    regular = found.regular
    list_depth = SECTION + 1
    return {
        "examined_entries": listing.examined,
        "unparsed_entries": listing.unparsed,
        "tree_truncated": listing.truncation is not None,
        "tree_truncation": listing.truncation,
        "counts": {
            "files": len(regular["file"]),
            "symlinks": len(regular["symlink"]),
            "submodules": len(regular["submodule"]),
            "other": len(regular["other"]),
            **found.counts,
        },
        "extensions": extensions(regular["file"], reached),
        "files": path_list(
            [(entry.path, entry.size) for entry in regular["file"]],
            depth=list_depth,
            reached=reached,
            sizes=True,
        ),
        "symlinks": path_list(
            [(entry.path, None) for entry in regular["symlink"]], depth=list_depth, reached=reached
        ),
        "submodules": path_list(
            [(entry.path, None) for entry in regular["submodule"]],
            depth=list_depth,
            reached=reached,
        ),
        "excluded_directories": dir_list(found.excluded, depth=list_depth, reached=reached),
    }


def _presence(
    listing: Listing, files: list[bytes], directories: dict[bytes, int], reached: Reached
) -> dict[str, Any]:
    # Classification covers only the examined entries (docs/python-package.md 2.2).
    return {
        "classification_complete": listing.truncation is None and listing.unparsed == 0,
        "classification_truncation": listing.truncation,
        "files": path_list([(path, None) for path in files], depth=SECTION + 1, reached=reached),
        "directories": dir_list(directories, depth=SECTION + 1, reached=reached),
    }


def sensitive_files(listing: Listing, found: Survey, reached: Reached) -> dict[str, Any]:
    return _presence(listing, found.sensitive_files, found.sensitive_directories, reached)


def agent_components(listing: Listing, found: Survey, reached: Reached) -> dict[str, Any]:
    return _presence(listing, found.agent_files, found.agent_directories, reached)


# -- allowlisted file contents --------------------------------------------------------


def _depth(path: bytes) -> int:
    return path.count(b"/")


def selection_queues(candidates: dict[str, list[TreeEntry]]) -> dict[str, deque[TreeEntry]]:
    """Each category ordered by literal path depth, then the path bytes."""
    return {
        category: deque(sorted(entries, key=lambda entry: (_depth(entry.path), entry.path)))
        for category, entries in candidates.items()
    }


def _lines(data: bytes, bytes_truncated: bool) -> tuple[list[str], bool]:
    if data.startswith(BOM):
        data = data[len(BOM) :]
    text, replaced = budget.decode(data, final=not bytes_truncated)
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines], replaced


def content_file(
    entry: TreeEntry, category: str, data: bytes, room: int, reached: Reached
) -> tuple[dict[str, Any], bool]:
    """One included file. Returns the object and whether ``room`` ran out."""
    size = entry.size if entry.size is not None else len(data)
    truncation: list[str] = []
    bytes_truncated = size > MAX_FILE_BYTES
    if bytes_truncated:
        truncation.append("MAX_FILE_BYTES")
    lines, replaced = _lines(data[:MAX_FILE_BYTES], bytes_truncated)
    if len(lines) > MAX_FILE_LINES:
        lines = lines[:MAX_FILE_LINES]
        truncation.append("MAX_FILE_LINES")
    content: list[str] = []
    truncated_lines: list[int] = []
    chars = 0
    exhausted = False
    overhead = budget.item_overhead(CONTENT_LINE)
    for index, line in enumerate(lines):
        fitted, cut = budget.fit(line, overhead)
        cost = budget.line_chars(fitted, CONTENT_LINE)
        if chars + cost > room:
            truncation.append("MAX_TOTAL_CONTENT_CHARS")
            exhausted = True
            break
        content.append(fitted)
        chars += cost
        if cut:
            truncated_lines.append(index)
    if truncated_lines:
        truncation.append(budget.LIMIT_LINE)
    truncation = ordered(truncation)
    reached.add(*truncation)
    path, path_truncated, path_replaced = _path_text(
        entry.path, budget.keyed_overhead(FILE_KEY, "path")
    )
    if path_truncated:
        reached.add(budget.LIMIT_LINE)
    return {
        "path": path,
        "path_truncated": path_truncated,
        "path_replaced": path_replaced,
        "category": category,
        "size": size,
        "decoding_replaced": replaced,
        "truncated": bool(truncation),
        "truncation": truncation,
        "truncated_lines": truncated_lines,
        "content_chars": chars,
        "content": content,
    }, exhausted


def contents(found: Survey, source: TreeSource, reached: Reached) -> dict[str, Any]:
    """Select allowlisted files by category round-robin and read them.

    Each round takes the next candidate of every category in priority order. A
    blob above ``MAX_BLOB_BYTES`` is skipped by its listed size without being
    read and without consuming a read. Every other candidate consumes one of
    ``MAX_CONTENT_FILES`` reads, including a binary one.
    """
    queues = selection_queues(found.candidates)
    total = sum(len(queue) for queue in queues.values())
    left_out: dict[str, list[TreeEntry]] = {reason: [] for reason in NOT_INCLUDED}
    files: list[dict[str, Any]] = []
    reads = 0
    room = MAX_TOTAL_CONTENT_CHARS
    exhausted = False
    while any(queues.values()):
        for category in classify.CATEGORIES:
            queue = queues[category]
            while queue:
                entry = queue.popleft()
                if entry.size is None or entry.size > MAX_BLOB_BYTES:
                    left_out["MAX_BLOB_BYTES"].append(entry)
                    continue
                if exhausted or room <= 0:
                    exhausted = True
                    left_out["MAX_TOTAL_CONTENT_CHARS"].append(entry)
                elif reads >= MAX_CONTENT_FILES:
                    left_out["MAX_CONTENT_FILES"].append(entry)
                else:
                    reads += 1
                    data = source.read(entry, READ_LIMIT)
                    if b"\x00" in data[:BINARY_SNIFF_BYTES]:
                        left_out["binary"].append(entry)
                    else:
                        included, exhausted = content_file(entry, category, data, room, reached)
                        files.append(included)
                        room -= included["content_chars"]
                break
    truncation = [reason for reason in NOT_INCLUDED[1:] if left_out[reason]]
    if exhausted:
        truncation.append("MAX_TOTAL_CONTENT_CHARS")
    truncation = ordered(truncation)
    reached.add(*truncation)
    depth = SECTION + 2
    return {
        "candidates": total,
        "read": reads,
        "included": len(files),
        "total_chars": sum(file["content_chars"] for file in files),
        "truncated": bool(truncation),
        "truncation": truncation,
        "files": files,
        "not_included": {
            reason: path_list(
                [(entry.path, None) for entry in entries], depth=depth, reached=reached
            )
            for reason, entries in left_out.items()
        },
    }


# -- Git facts -----------------------------------------------------------------------


def log_args(commit: str) -> list[str]:
    """``log`` arguments after ``--no-show-signature``, which ``git.build_argv`` adds."""
    if not OBJECT_ID.fullmatch(commit):
        raise ValueError("invalid object ID")
    return [
        "--no-mailmap",
        "--no-decorate",
        "--no-color",
        f"--max-count={RECENT_COMMITS}",
        LOG_FORMAT,
        "--end-of-options",
        commit,
    ]


SHALLOW_ARGS: Final = ("--is-shallow-repository",)


def is_shallow(repository: AcquiredRepository) -> bool:
    result = repository.read("rev-parse", list(SHALLOW_ARGS), max_output_bytes=GIT_MAX_OUTPUT_BYTES)
    value = result.stdout.rstrip(b"\r\n")
    if result.exit_code != 0 or result.stdout_truncated or value not in {b"true", b"false"}:
        raise CommandError(ErrorCode.INTERNAL_ERROR)
    return value == b"true"


def _commit(fields: Sequence[bytes], partial: bool, reached: Reached) -> dict[str, Any] | None:
    try:
        commit = fields[0].decode("ascii")
        date: str | None = fields[1].decode("ascii")
    except UnicodeDecodeError:
        return None
    if not OBJECT_ID.fullmatch(commit):
        return None
    if date is not None and not _DATE.fullmatch(date):
        date = None
    subject, replaced = budget.decode(fields[2].split(b"\x00", 1)[0], final=not partial)
    truncated = partial
    if len(subject) > MAX_COMMIT_SUBJECT_CHARS:
        subject = subject[:MAX_COMMIT_SUBJECT_CHARS]
        truncated = True
        reached.add("MAX_COMMIT_SUBJECT_CHARS")
    subject, cut = budget.fit(subject, budget.keyed_overhead(COMMIT_KEY, "subject"))
    if cut:
        reached.add(budget.LIMIT_LINE)
    return {
        "commit": commit,
        "date": date,
        "subject": subject,
        "subject_truncated": truncated or cut,
        "subject_replaced": replaced,
    }


def parse_log(stdout: bytes, truncated: bool, reached: Reached) -> tuple[list[dict[str, Any]], int]:
    """Parse ``LOG_FORMAT`` records. A partial last record keeps its commit and date."""
    records = stdout.split(LOG_RECORD_END)
    tail = records.pop()
    commits: list[dict[str, Any]] = []
    unparsed = 0
    for record in records:
        fields = record.split(b"\x00")
        parsed = _commit(fields, False, reached) if len(fields) == 3 else None
        if parsed is None:
            unparsed += 1
        else:
            commits.append(parsed)
    if tail:
        fields = tail.split(b"\x00", 2)
        parsed = _commit(fields, True, reached) if truncated and len(fields) == 3 else None
        if parsed is not None:
            commits.append(parsed)
        elif not truncated:
            unparsed += 1
    return commits[:RECENT_COMMITS], unparsed


def recent_commits(repository: AcquiredRepository, reached: Reached) -> dict[str, Any]:
    result = repository.read(
        "log", log_args(repository.commit), max_output_bytes=GIT_MAX_OUTPUT_BYTES
    )
    if result.exit_code != 0:
        raise CommandError(ErrorCode.INTERNAL_ERROR)
    own = Reached()
    commits, unparsed = parse_log(result.stdout, result.stdout_truncated, own)
    if result.stdout_truncated:
        own.add("GIT_MAX_OUTPUT_BYTES")
    if len(commits) >= RECENT_COMMITS:
        own.add("RECENT_COMMITS")
    truncation = ordered(own.names)
    reached.add(*truncation)
    return {
        "requested": RECENT_COMMITS,
        "returned": len(commits),
        "unparsed": unparsed,
        "truncated": bool(truncation),
        "truncation": truncation,
        "recent_commits": commits,
    }


# -- evidence -----------------------------------------------------------------------


def evidence(
    source: TreeSource, repository: AcquiredRepository, reached: Reached
) -> dict[str, Any]:
    """Collect ``result.evidence`` in its documented section order."""
    listing = source.listing()
    found = survey(listing)
    return {
        "inventory": inventory(listing, found, reached),
        "sensitive_files": sensitive_files(listing, found, reached),
        "agent_components": agent_components(listing, found, reached),
        "contents": contents(found, source, reached),
        "git": recent_commits(repository, reached),
    }

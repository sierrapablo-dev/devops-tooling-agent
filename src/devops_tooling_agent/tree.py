"""The source-neutral tree that collectors consume (ADR-0006 Section 11).

A tree source lists entries with their path, type, and size, and reads the
content of a file entry through a bounded reader. Collectors never see how the
source obtains them. ``GitTreeSource`` lists the tree of the fetched commit and
reads blobs by object ID (RR-14): no path controlled by the target is ever
passed to Git, and symbolic links are never read or resolved.
"""

import re
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from devops_tooling_agent.acquire import AcquiredRepository
from devops_tooling_agent.constants import MAX_TREE_ENTRIES, MAX_TREE_OUTPUT_BYTES
from devops_tooling_agent.errors import CommandError, ErrorCode

EntryType = Literal["file", "symlink", "submodule", "other"]

OBJECT_ID: Final = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.ASCII)
_MODE: Final = re.compile(rb"[0-7]{6}")
_SIZE: Final = re.compile(rb"[0-9]{1,20}")
# Tree modes (git ls-tree): a mode and its object type give the entry type.
_TYPES: Final[dict[tuple[bytes, bytes], EntryType]] = {
    (b"100644", b"blob"): "file",
    (b"100755", b"blob"): "file",
    (b"120000", b"blob"): "symlink",
    (b"160000", b"commit"): "submodule",
}

TRUNCATION_ENTRIES: Final = "MAX_TREE_ENTRIES"
TRUNCATION_OUTPUT: Final = "MAX_TREE_OUTPUT_BYTES"


@dataclass(frozen=True)
class TreeEntry:
    """One entry. ``key`` is opaque to collectors; ``size`` is ``None`` when unknown."""

    path: bytes
    type: EntryType
    size: int | None
    key: str


@dataclass(frozen=True)
class Listing:
    """The examined entries, in source order.

    ``examined`` counts every record examined, including ``unparsed`` ones.
    ``truncation`` names the limit that stopped the listing, or is ``None``
    when every entry was examined.
    """

    entries: list[TreeEntry]
    examined: int
    unparsed: int
    truncation: str | None


class TreeSource(Protocol):
    def listing(self) -> Listing: ...

    def read(self, entry: TreeEntry, limit: int) -> bytes:
        """At most ``limit`` bytes of a ``file`` entry's content."""
        ...


def ls_tree_args(commit: str) -> list[str]:
    if not OBJECT_ID.fullmatch(commit):
        raise ValueError("invalid object ID")
    return ["-r", "-z", "--long", "--full-tree", "--end-of-options", commit]


def cat_file_args(object_id: str) -> list[str]:
    if not OBJECT_ID.fullmatch(object_id):
        raise ValueError("invalid object ID")
    return ["--end-of-options", "blob", object_id]


def parse_record(record: bytes) -> TreeEntry | None:
    """Parse one ``ls-tree -z --long`` record: ``<mode> <type> <oid> <size>\\t<path>``."""
    meta, separator, path = record.partition(b"\t")
    if not separator or not path:
        return None
    fields = meta.split()
    if len(fields) != 4:
        return None
    mode, object_type, object_id, size = fields
    if not _MODE.fullmatch(mode):
        return None
    try:
        key = object_id.decode("ascii")
    except UnicodeDecodeError:
        return None
    if not OBJECT_ID.fullmatch(key):
        return None
    if size == b"-":
        parsed_size = None
    elif _SIZE.fullmatch(size):
        parsed_size = int(size)
    else:
        return None
    entry_type = _TYPES.get((mode, object_type), "other")
    return TreeEntry(path, entry_type, parsed_size, key)


def parse_listing(stdout: bytes, truncated: bool, *, max_entries: int) -> Listing:
    """Parse a NUL-terminated listing. A partial last record is discarded."""
    records = stdout.split(b"\x00")
    # The part after the last NUL is empty for a complete listing, and a
    # partial record when the capture was truncated. Either way it is dropped.
    records.pop()
    truncation = TRUNCATION_OUTPUT if truncated else None
    if len(records) > max_entries:
        records = records[:max_entries]
        truncation = TRUNCATION_ENTRIES
    entries: list[TreeEntry] = []
    unparsed = 0
    for record in records:
        entry = parse_record(record)
        if entry is None:
            unparsed += 1
        else:
            entries.append(entry)
    return Listing(entries, len(records), unparsed, truncation)


def _read_failure() -> CommandError:
    # The fetched objects passed the object and connectivity checks, so a
    # failing read is unexpected (docs/python-package.md, Section 2.2).
    return CommandError(ErrorCode.INTERNAL_ERROR)


class GitTreeSource:
    """The tree of the fetched commit, read through the handle's read commands."""

    def __init__(self, repository: AcquiredRepository) -> None:
        self._repository = repository

    def listing(self) -> Listing:
        result = self._repository.read(
            "ls-tree",
            ls_tree_args(self._repository.commit),
            max_output_bytes=MAX_TREE_OUTPUT_BYTES,
        )
        if result.exit_code != 0:
            raise _read_failure()
        return parse_listing(result.stdout, result.stdout_truncated, max_entries=MAX_TREE_ENTRIES)

    def read(self, entry: TreeEntry, limit: int) -> bytes:
        if entry.type != "file":
            raise ValueError("only file entries are read")
        result = self._repository.read("cat-file", cat_file_args(entry.key), max_output_bytes=limit)
        if result.exit_code != 0:
            raise _read_failure()
        return result.stdout[:limit]

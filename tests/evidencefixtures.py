"""In-memory evidence fixtures: a fake tree source and synthetic results.

They exercise the collectors and the budget without Git, gh, or a workspace.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from devops_tooling_agent import collect, collectors
from devops_tooling_agent.envelope import serialized_length, success
from devops_tooling_agent.tree import EntryType, Listing, TreeEntry

COMMIT = "c" * 40


def entry(path: bytes, data: bytes = b"x\n", entry_type: EntryType = "file") -> TreeEntry:
    key = hashlib.sha1(path + data, usedforsecurity=False).hexdigest()
    return TreeEntry(path, entry_type, len(data), key)


@dataclass
class FakeSource:
    """A tree source over in-memory entries. Records every read."""

    files: Mapping[bytes, bytes]
    extra: Sequence[TreeEntry] = ()
    truncation: str | None = None
    reads: list[bytes] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._entries = [entry(path, data) for path, data in self.files.items()]
        self._entries += list(self.extra)
        self._data = {item.key: self.files.get(item.path, b"") for item in self._entries}

    def listing(self) -> Listing:
        return Listing(list(self._entries), len(self._entries), 0, self.truncation)

    def read(self, item: TreeEntry, limit: int) -> bytes:
        assert item.type == "file"
        self.reads.append(item.path)
        return self._data[item.key][:limit]


def log_stdout(subjects: Sequence[bytes]) -> bytes:
    return b"".join(
        f"{index:040x}".encode() + b"\x00" + b"2023-11-14T22:13:20+00:00\x00" + subject + b"\x00\n"
        for index, subject in enumerate(subjects)
    )


def build_evidence(
    source: FakeSource, subjects: Sequence[bytes] = (b"subject",)
) -> tuple[dict[str, Any], collectors.Reached]:
    reached = collectors.Reached()
    listing = source.listing()
    found = collectors.survey(listing)
    commits, unparsed = collectors.parse_log(log_stdout(subjects), False, reached)
    evidence = {
        "inventory": collectors.inventory(listing, found, reached),
        "sensitive_files": collectors.sensitive_files(listing, found, reached),
        "agent_components": collectors.agent_components(listing, found, reached),
        "contents": collectors.contents(found, source, reached),
        "git": {
            "requested": 20,
            "returned": len(commits),
            "unparsed": unparsed,
            "truncated": False,
            "truncation": [],
            "recent_commits": commits,
        },
    }
    return evidence, reached


REPOSITORY = {
    "name_with_owner": "octocat/Hello-World",
    "node_id": "MDEwOlJlcG9zaXRvcnkxMjk2MjY5",
    "url": "https://github.com/octocat/Hello-World",
    "visibility": "public",
    "default_branch": "master",
    "viewer_permission": "read",
    "archived": False,
    "empty": False,
    "disk_usage_kib": 1,
}


def build_result(source: FakeSource, subjects: Sequence[bytes] = (b"subject",)) -> dict[str, Any]:
    evidence, reached = build_evidence(source, subjects)
    return {
        "repository": dict(REPOSITORY),
        "acquisition": {
            "commit": COMMIT,
            "reference": "refs/heads/master",
            "depth": 20,
            "shallow": True,
        },
        "collector": collect.collector_section(reached, 0),
        "evidence": evidence,
    }


def envelope_length(result: dict[str, Any], warnings: Sequence[Mapping[str, Any]] = ()) -> int:
    return serialized_length(
        success(collect.COMMAND, result, warnings=warnings, untrusted=collect.UNTRUSTED)
    )


def large_source(files: int = 1500, content_files: int = 70) -> FakeSource:
    """Many listed paths, long paths, and more allowlisted files than can be read."""
    data: dict[bytes, bytes] = {}
    for index in range(files):
        data[f"src/module{index:05d}/{'n' * 120}.py".encode()] = b"x\n"
    for index in range(content_files):
        data[f"packages/p{index:03d}/package.json".encode()] = (
            b'{"name": "' + b"v" * 400 + b'"}\n'
        ) * 40
    for index in range(30):
        data[f"links/{index}.txt".encode()] = b"x"
    extra = [entry(f"vendor-links/l{index}".encode(), b"t", "symlink") for index in range(200)]
    extra += [entry(f"modules/m{index}".encode(), b"", "submodule") for index in range(200)]
    extra += [entry(f"node_modules/p{index}/i.js".encode()) for index in range(300)]
    extra += [entry(f"config/{index}.env".encode()) for index in range(200)]
    extra += [entry(f".env.{index}".encode()) for index in range(300)]
    extra += [entry(f"s{index}/secrets.d/key".encode()) for index in range(100)]
    extra += [entry(f"pkg{index}/CLAUDE.md".encode()) for index in range(300)]
    extra += [entry(f"pkg{index}/.claude/settings.json".encode()) for index in range(300)]
    return FakeSource(data, extra)

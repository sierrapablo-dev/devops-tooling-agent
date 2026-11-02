"""Collectors and the tree source, without Git (ADR-0006 Sections 9 and 11)."""

import pytest

from devops_tooling_agent import collectors, tree
from devops_tooling_agent.constants import (
    BINARY_SNIFF_BYTES,
    MAX_BLOB_BYTES,
    MAX_COMMIT_SUBJECT_CHARS,
    MAX_CONTENT_FILES,
    MAX_EXTENSIONS,
    MAX_FILE_BYTES,
    MAX_FILE_LINES,
    MAX_INVENTORY_PATHS,
    RECENT_COMMITS,
)
from devops_tooling_agent.tree import Listing, TreeEntry
from evidencefixtures import FakeSource, build_evidence, entry, log_stdout

OID = "a" * 40


# -- tree records ---------------------------------------------------------------------


def record(mode: str, kind: str, size: str, path: bytes, oid: str = OID) -> bytes:
    return f"{mode} {kind} {oid} {size:>7}\t".encode() + path + b"\x00"


@pytest.mark.parametrize(
    ("mode", "kind", "size", "expected_type", "expected_size"),
    [
        ("100644", "blob", "12", "file", 12),
        ("100755", "blob", "0", "file", 0),
        ("120000", "blob", "9", "symlink", 9),
        ("160000", "commit", "-", "submodule", None),
        ("040000", "tree", "-", "other", None),
        ("100664", "blob", "3", "other", 3),
        ("120000", "commit", "-", "other", None),
    ],
)
def test_tree_modes(
    mode: str, kind: str, size: str, expected_type: str, expected_size: int | None
) -> None:
    listing = tree.parse_listing(record(mode, kind, size, b"p"), False, max_entries=10)
    assert listing.entries == [TreeEntry(b"p", expected_type, expected_size, OID)]  # type: ignore[arg-type]


def test_tree_paths_are_bytes_verbatim() -> None:
    paths = [b"-n.json", b"a b/c\td", b"bad\xff\xfe", b"\xe2\x80\xaebidi", b"x" * 4000]
    stdout = b"".join(record("100644", "blob", "1", path) for path in paths)
    listing = tree.parse_listing(stdout, False, max_entries=10)
    assert [item.path for item in listing.entries] == paths
    assert listing.truncation is None and listing.examined == 5


def test_tree_partial_last_record_is_discarded_on_truncation() -> None:
    stdout = record("100644", "blob", "1", b"a") + record("100644", "blob", "1", b"b")[:20]
    listing = tree.parse_listing(stdout, True, max_entries=10)
    assert [item.path for item in listing.entries] == [b"a"]
    assert listing.truncation == "MAX_TREE_OUTPUT_BYTES"


def test_tree_entry_limit() -> None:
    stdout = b"".join(record("100644", "blob", "1", f"f{i}".encode()) for i in range(5))
    listing = tree.parse_listing(stdout, False, max_entries=3)
    assert listing.examined == 3 and listing.truncation == "MAX_TREE_ENTRIES"


@pytest.mark.parametrize(
    "bad",
    [
        b"100644 blob " + OID.encode() + b" 1 nopath",
        b"100644 blob " + OID.encode() + b"\tno-size",
        b"10064 blob " + OID.encode() + b" 1\tp",
        b"100644 blob " + b"g" * 40 + b" 1\tp",
        b"100644 blob " + OID.encode() + b" x\tp",
        b"100644 blob " + OID.encode() + b" 1\t",
    ],
)
def test_malformed_records_are_unparsed(bad: bytes) -> None:
    listing = tree.parse_listing(bad + b"\x00", False, max_entries=10)
    assert listing.entries == [] and listing.unparsed == 1 and listing.examined == 1


def test_git_arguments_accept_only_object_ids() -> None:
    assert tree.ls_tree_args(OID) == ["-r", "-z", "--long", "--full-tree", "--end-of-options", OID]
    assert tree.cat_file_args("b" * 64) == ["--end-of-options", "blob", "b" * 64]
    for bad in ("HEAD", "-n", "A" * 40, "a" * 39, OID + "\n", "refs/heads/main"):
        with pytest.raises(ValueError):
            tree.ls_tree_args(bad)
        with pytest.raises(ValueError):
            tree.cat_file_args(bad)
        with pytest.raises(ValueError):
            collectors.log_args(bad)


def test_log_arguments() -> None:
    assert collectors.log_args(OID) == [
        "--no-mailmap",
        "--no-decorate",
        "--no-color",
        f"--max-count={RECENT_COMMITS}",
        "--format=%H%x00%cI%x00%s%x00",
        "--end-of-options",
        OID,
    ]


# -- selection --------------------------------------------------------------------------


def _included(source: FakeSource) -> list[str]:
    evidence, _ = build_evidence(source)
    return [file["path"] for file in evidence["contents"]["files"]]


def test_many_terraform_files_cannot_exclude_the_root_readme_and_manifest() -> None:
    files = {f"modules/m{i:02d}/main.tf".encode(): b"x\n" for i in range(80)}
    files[b"README.md"] = b"# r\n"
    files[b"package.json"] = b"{}\n"
    included = _included(FakeSource(files))
    assert "README.md" in included and "package.json" in included
    assert len(included) == MAX_CONTENT_FILES


def test_many_workflows_cannot_exclude_the_root_readme_and_manifest() -> None:
    files = {f".github/workflows/w{i:02d}.yml".encode(): b"on: push\n" for i in range(60)}
    files[b"README.md"] = b"# r\n"
    files[b"pyproject.toml"] = b"[project]\n"
    included = _included(FakeSource(files))
    assert "README.md" in included and "pyproject.toml" in included


def test_round_robin_order() -> None:
    files = {
        b"z/package.json": b"{}\n",
        b"package.json": b"{}\n",
        b"a/b/package.json": b"{}\n",
        b"infra/main.tf": b"x\n",
        b"Dockerfile": b"FROM x\n",
        b".editorconfig": b"x\n",
        b"README.md": b"x\n",
        b".github/workflows/ci.yml": b"x\n",
        b".github/workflows/b.yml": b"x\n",
    }
    assert _included(FakeSource(files)) == [
        # Round 1: one per category in priority order, each by depth then bytes.
        ".github/workflows/b.yml",
        "package.json",
        "infra/main.tf",
        "Dockerfile",
        ".editorconfig",
        "README.md",
        # Round 2.
        ".github/workflows/ci.yml",
        "z/package.json",
        # Round 3.
        "a/b/package.json",
    ]


def test_blob_above_the_limit_is_skipped_without_a_read_or_slot() -> None:
    files = {f"p{i:02d}/package.json".encode(): b"{}\n" for i in range(MAX_CONTENT_FILES)}
    big = b"x" * (MAX_BLOB_BYTES + 1)
    files[b"README.md"] = big
    source = FakeSource(files)
    evidence, reached = build_evidence(source)
    contents = evidence["contents"]
    assert b"README.md" not in source.reads
    assert contents["read"] == MAX_CONTENT_FILES == contents["included"]
    assert contents["not_included"]["MAX_BLOB_BYTES"]["paths"] == ["README.md"]
    assert "MAX_BLOB_BYTES" in contents["truncation"] and "MAX_BLOB_BYTES" in reached.names


def test_binary_candidate_consumes_a_read() -> None:
    files = {f"p{i:02d}/package.json".encode(): b"{}\n" for i in range(MAX_CONTENT_FILES + 1)}
    files[b"p00/package.json"] = b"{" + b"\x00" * 4 + b"}"
    evidence, _ = build_evidence(FakeSource(files))
    contents = evidence["contents"]
    assert contents["read"] == MAX_CONTENT_FILES
    assert contents["included"] == MAX_CONTENT_FILES - 1
    assert contents["not_included"]["binary"]["paths"] == ["p00/package.json"]
    assert contents["not_included"]["MAX_CONTENT_FILES"]["paths"] == ["p50/package.json"]
    assert "MAX_CONTENT_FILES" in contents["truncation"]


def test_nul_beyond_the_sniff_prefix_is_not_binary() -> None:
    data = b"a" * BINARY_SNIFF_BYTES + b"\x00"
    evidence, _ = build_evidence(FakeSource({b"package.json": data}))
    assert evidence["contents"]["included"] == 1


def test_only_regular_files_are_candidates() -> None:
    extra = [
        entry(b"link/package.json", b"README.md", "symlink"),
        entry(b"sub/package.json", b"", "submodule"),
    ]
    source = FakeSource({b".claude/package.json": b"{}", b".env": b"x"}, extra)
    evidence, _ = build_evidence(source)
    assert evidence["contents"]["candidates"] == 0 and source.reads == []


# -- content handling ----------------------------------------------------------------------


def _file(data: bytes, size: int | None = None, room: int = 10**9) -> dict[str, object]:
    item = TreeEntry(b"README.md", "file", len(data) if size is None else size, OID)
    reached = collectors.Reached()
    result, _ = collectors.content_file(item, "documentation", data, room, reached)
    return result


def test_bom_crlf_and_trailing_newline() -> None:
    result = _file(b"\xef\xbb\xbfone\r\ntwo\nthree\r\n")
    assert result["content"] == ["one", "two", "three"]
    assert result["truncation"] == [] and result["decoding_replaced"] is False


def test_lone_carriage_return_stays_in_the_line() -> None:
    assert _file(b"a\rb\n")["content"] == ["a\rb"]


def test_decoding_replacement_is_recorded() -> None:
    result = _file(b"ok\nbad\xff\n")
    assert result["content"] == ["ok", "bad�"] and result["decoding_replaced"] is True


def test_byte_limit_cut_inside_a_sequence_is_not_a_replacement() -> None:
    data = b"a" * (MAX_FILE_BYTES - 1) + "é".encode()
    result = _file(data[:MAX_FILE_BYTES], size=len(data))
    assert result["decoding_replaced"] is False
    assert result["truncation"] == ["MAX_FILE_BYTES", "MAX_LINE_CHARS"]


def test_line_limit() -> None:
    result = _file(b"x\n" * (MAX_FILE_LINES + 5))
    assert len(result["content"]) == MAX_FILE_LINES  # type: ignore[arg-type]
    assert result["truncation"] == ["MAX_FILE_LINES"]


def test_long_lines_are_truncated_and_recorded() -> None:
    result = _file(b"short\n" + b"\xe2\x80\xae" * 2000 + b"\n")
    assert result["truncated_lines"] == [1]
    assert result["truncation"] == ["MAX_LINE_CHARS"]


def test_total_content_room() -> None:
    result = _file(b"x\n" * 100, room=200)
    assert "MAX_TOTAL_CONTENT_CHARS" in result["truncation"]  # type: ignore[operator]
    assert result["content_chars"] <= 200  # type: ignore[operator]


def test_total_content_budget_moves_later_files_out() -> None:
    files = {f"p{i:02d}/package.json".encode(): b"y" * 400 + b"\n" for i in range(5)}
    files.update({f"p{i:02d}/README.md".encode(): (b"z" * 400 + b"\n") * 200 for i in range(9)})
    evidence, reached = build_evidence(FakeSource(files))
    contents = evidence["contents"]
    assert contents["total_chars"] <= 100000
    assert contents["not_included"]["MAX_TOTAL_CONTENT_CHARS"]["total"] > 0
    assert "MAX_TOTAL_CONTENT_CHARS" in contents["truncation"]
    assert "MAX_TOTAL_CONTENT_CHARS" in reached.names


# -- inventory ---------------------------------------------------------------------------


def test_extension_counts_exclude_excluded_sensitive_and_agent_entries() -> None:
    files = {
        b"a.py": b"x",
        b"b.PY": b"x",
        b"Makefile": b"x",
        b".gitignore": b"x",
        b"x." + b"e" * 17: b"x",
        b"x.caf\xc3\xa9": b"x",
        b"node_modules/a.js": b"x",
        b"build/out.o": b"x",
        b"key.pem": b"x",
        b".claude/s.json": b"x",
        b"CLAUDE.md": b"x",
    }
    evidence, _ = build_evidence(FakeSource(files))
    extensions = evidence["inventory"]["extensions"]
    assert extensions["counts"] == {".py": 2}
    assert extensions["none"] == 2 and extensions["other"] == 2
    counts = evidence["inventory"]["counts"]
    assert counts == {
        "files": 6,
        "symlinks": 0,
        "submodules": 0,
        "other": 0,
        "sensitive": 1,
        "agent_components": 2,
        "excluded": 2,
    }


def test_extension_key_limit() -> None:
    files = {f"f{i}.e{i}".encode(): b"x" for i in range(MAX_EXTENSIONS + 5)}
    files[b"z.e0"] = b"x"
    evidence, reached = build_evidence(FakeSource(files))
    extensions = evidence["inventory"]["extensions"]
    assert len(extensions["counts"]) == MAX_EXTENSIONS
    assert next(iter(extensions["counts"])) == ".e0"  # most frequent first
    assert extensions["other"] == 5 and extensions["truncation"] == ["MAX_EXTENSIONS"]
    assert "MAX_EXTENSIONS" in reached.names


def test_inventory_lists_sorted_bounded_paths_with_sizes() -> None:
    files = {f"d/{i:05d}".encode(): b"x" * (i % 7) for i in range(MAX_INVENTORY_PATHS + 3)}
    evidence, reached = build_evidence(FakeSource(files))
    listed = evidence["inventory"]["files"]
    assert listed["total"] == MAX_INVENTORY_PATHS + 3
    assert listed["listed"] == len(listed["paths"]) == len(listed["sizes"]) == MAX_INVENTORY_PATHS
    assert listed["paths"] == sorted(listed["paths"])
    assert listed["sizes"][:8] == [0, 1, 2, 3, 4, 5, 6, 0]
    assert listed["truncation"] == ["MAX_INVENTORY_PATHS"]
    assert "MAX_INVENTORY_PATHS" in reached.names


def test_paths_are_sorted_by_bytes_and_alterations_recorded() -> None:
    files = {b"\xff.txt": b"x", b"b.txt": b"x", b"\xc3\xa9.txt": b"x", b"a" * 900: b"x"}
    evidence, reached = build_evidence(FakeSource(files))
    listed = evidence["inventory"]["files"]
    assert listed["paths"][1:] == ["b.txt", "é.txt", "�.txt"]
    assert listed["truncated_path_indexes"] == [0]
    assert listed["replaced_path_indexes"] == [3]
    assert "MAX_LINE_CHARS" in reached.names


def test_agent_directories_report_presence_only() -> None:
    files = {
        b".claude/skills/deploy/SKILL.md": b"x",
        b".claude/settings.json": b"x",
        b"pkg/.cursor/rules/r.mdc": b"x",
        b"AGENTS.md": b"x",
    }
    evidence, _ = build_evidence(FakeSource(files))
    agents = evidence["agent_components"]
    assert agents["files"]["paths"] == ["AGENTS.md"]
    assert agents["directories"]["directories"] == [
        {"path": ".claude", "path_truncated": False, "path_replaced": False, "entries": 2},
        {
            "path": "pkg/.cursor/rules",
            "path_truncated": False,
            "path_replaced": False,
            "entries": 1,
        },
    ]
    serialized = repr(evidence)
    assert "SKILL.md" not in serialized and "settings.json" not in serialized


@pytest.mark.parametrize("truncation", ["MAX_TREE_ENTRIES", "MAX_TREE_OUTPUT_BYTES"])
def test_tree_truncation_marks_classification_as_partial(truncation: str) -> None:
    evidence, reached = build_evidence(FakeSource({b".env": b"x"}, truncation=truncation))
    for section in ("sensitive_files", "agent_components"):
        assert evidence[section]["classification_complete"] is False
        assert evidence[section]["classification_truncation"] == truncation
    assert evidence["inventory"]["tree_truncated"] is True
    assert truncation in reached.names


def test_unparsed_records_mark_classification_as_partial() -> None:
    listing = Listing([], 1, 1, None)
    found = collectors.survey(listing)
    section = collectors.sensitive_files(listing, found, collectors.Reached())
    assert section["classification_complete"] is False
    assert section["classification_truncation"] is None


# -- recent commits --------------------------------------------------------------------------


def test_log_records() -> None:
    reached = collectors.Reached()
    commits, unparsed = collectors.parse_log(log_stdout([b"one", b"two"]), False, reached)
    assert [commit["subject"] for commit in commits] == ["one", "two"]
    assert commits[0]["date"] == "2023-11-14T22:13:20+00:00"
    assert unparsed == 0


def test_log_subject_limits() -> None:
    reached = collectors.Reached()
    subjects = [b"s" * (MAX_COMMIT_SUBJECT_CHARS + 1), b"\xe2\x80\xae" * 150, b"bad\xff"]
    commits, _ = collectors.parse_log(log_stdout(subjects), False, reached)
    assert len(commits[0]["subject"]) == MAX_COMMIT_SUBJECT_CHARS
    assert commits[0]["subject_truncated"] is True
    assert commits[1]["subject_truncated"] is True  # 150 escapes exceed the line
    assert commits[2]["subject_replaced"] is True
    assert {"MAX_COMMIT_SUBJECT_CHARS", "MAX_LINE_CHARS"} <= reached.names


def test_log_partial_record_keeps_commit_and_date() -> None:
    stdout = log_stdout([b"x" * 100000])[:65536]
    commits, unparsed = collectors.parse_log(stdout, True, collectors.Reached())
    assert len(commits) == 1 and commits[0]["subject_truncated"] is True
    assert unparsed == 0


def test_log_malformed_records() -> None:
    stdout = b"zz\x00date\x00s\x00\n" + log_stdout([b"ok"]) + b"tail-without-end"
    commits, unparsed = collectors.parse_log(stdout, False, collectors.Reached())
    assert [commit["subject"] for commit in commits] == ["ok"] and unparsed == 2


def test_log_invalid_date_is_null() -> None:
    stdout = OID.encode() + b"\x00yesterday\x00s\x00\n"
    commits, _ = collectors.parse_log(stdout, False, collectors.Reached())
    assert commits[0]["date"] is None

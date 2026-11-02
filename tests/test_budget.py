"""Serialized line bounds and envelope budget reduction (ADR-0006 Section 9)."""

import copy
import json
from typing import Any

import pytest

from devops_tooling_agent import budget, collect, collectors
from devops_tooling_agent.constants import (
    MAX_BRANCH_NAME_CHARS,
    MAX_ENVELOPE_CHARS,
    MAX_LINE_CHARS,
    MAX_OWNER_CHARS,
    MAX_REPO_NAME_CHARS,
    MAX_SWEEP_ENTRIES,
)
from devops_tooling_agent.envelope import _serialize_unchecked, success
from devops_tooling_agent.workspace import NAME_LENGTH
from evidencefixtures import build_result, envelope_length, large_source


@pytest.mark.parametrize(
    "char",
    [
        "a",
        '"',
        "\\",
        "\n",
        "\r",
        "\t",
        "\b",
        "\f",
        "\x00",
        "\x1f",
        "\x7f",
        "é",
        "‮",
        "​",
        "�",
        "\U0001f600",
        "/",
    ],
)
def test_escaped_length_matches_json(char: str) -> None:
    assert budget.escaped_length(char) == len(json.dumps(char, ensure_ascii=True)) - 2


@pytest.mark.parametrize("depth", [1, 4, 7])
@pytest.mark.parametrize(
    "text",
    ["a" * 2000, "‮" * 400, "\U0001f600" * 200, '"' * 600, "abé" * 300, "short"],
)
def test_fit_keeps_every_line_within_the_bound(depth: int, text: str) -> None:
    for key in (None, "subject"):
        overhead = budget.item_overhead(depth) if key is None else budget.keyed_overhead(depth, key)
        fitted, truncated = budget.fit(text, overhead)
        assert text.startswith(fitted)
        assert truncated == (fitted != text)
        container: Any = [fitted] if key is None else {key: fitted}
        for _ in range(depth - 1):
            container = [container]
        lines = json.dumps(container, ensure_ascii=True, indent=2).split("\n")
        assert max(len(line) for line in lines) <= MAX_LINE_CHARS
        if truncated:
            # The next character would not have fitted.
            extra = budget.escaped_length(text[len(fitted)])
            assert overhead + sum(map(budget.escaped_length, fitted)) + extra > MAX_LINE_CHARS


def test_decode_records_replacement_but_not_a_cut_sequence() -> None:
    assert budget.decode(b"caf\xc3\xa9") == ("café", False)
    assert budget.decode(b"bad\xff") == ("bad�", True)
    # A sequence cut by a byte limit is dropped, not reported as replacement.
    assert budget.decode(b"caf\xc3", final=False) == ("caf", False)
    assert budget.decode(b"caf\xc3", final=True) == ("caf�", True)


# -- reduction ---------------------------------------------------------------------


def _reduce(result: dict[str, Any], limit: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget, "MAX_ENVELOPE_CHARS", limit)
    budget.reduce(result, envelope_length)


def _check_lists(result: dict[str, Any]) -> None:
    evidence = result["evidence"]
    path_lists = [
        evidence["inventory"]["files"],
        evidence["inventory"]["symlinks"],
        evidence["inventory"]["submodules"],
        evidence["sensitive_files"]["files"],
        evidence["agent_components"]["files"],
        *evidence["contents"]["not_included"].values(),
    ]
    for path_list in path_lists:
        assert path_list["listed"] == len(path_list["paths"]) <= path_list["total"]
        assert path_list["truncated"] == (path_list["listed"] < path_list["total"])
        for index in path_list["truncated_path_indexes"] + path_list["replaced_path_indexes"]:
            assert 0 <= index < path_list["listed"]
    files = evidence["inventory"]["files"]
    assert len(files["sizes"]) == len(files["paths"])


def test_sizes_stay_aligned_with_paths_under_every_reduction_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = large_source()
    original = build_result(source)
    full = envelope_length(original)
    sizes = dict(
        zip(
            original["evidence"]["inventory"]["files"]["paths"],
            original["evidence"]["inventory"]["files"]["sizes"],
            strict=True,
        )
    )
    seen: set[str] = set()
    limit = full - 1000
    while limit > 9000:
        result = copy.deepcopy(original)
        _reduce(result, limit, monkeypatch)
        assert envelope_length(result) <= limit
        _check_lists(result)
        files = result["evidence"]["inventory"]["files"]
        # Every kept size still belongs to its path.
        for path, size in zip(files["paths"], files["sizes"], strict=True):
            assert sizes[path] == size
        seen.update(result["collector"]["reductions"])
        limit -= 5000 if limit > 40000 else 1500
    # The sweep of budgets went through every section that the fixture fills.
    filled = set()
    for name, path, field, _ in budget.SECTIONS:
        node = original["evidence"]
        for key in path:
            node = node[key]
        if node[field]:
            filled.add(name)
    assert seen == filled
    assert len(filled) >= 12


def test_reduction_records_are_structural(monkeypatch: pytest.MonkeyPatch) -> None:
    result = build_result(large_source())
    _reduce(result, 60000, monkeypatch)
    collector = result["collector"]
    assert collector["reductions"][0] == "inventory.files"
    assert "MAX_ENVELOPE_CHARS" in collector["limits_reached"]
    files = result["evidence"]["inventory"]["files"]
    assert files["truncated"] and "MAX_ENVELOPE_CHARS" in files["truncation"]
    # Counts are never reduced.
    assert result["evidence"]["inventory"]["counts"]["files"] == files["total"]


def test_reduction_order_is_fixed() -> None:
    names = [name for name, *_ in budget.SECTIONS]
    assert names[0] == "inventory.files"
    assert names.index("contents.files") > names.index("contents.not_included.MAX_CONTENT_FILES")
    assert names[-2:] == ["sensitive_files.files", "sensitive_files.directories"]


def test_no_reduction_when_the_envelope_fits() -> None:
    result = build_result(large_source(files=10, content_files=2))
    before = copy.deepcopy(result)
    budget.reduce(result, envelope_length)
    assert result == before
    assert result["collector"]["reductions"] == []


def test_content_reduction_counts_the_removed_files(monkeypatch: pytest.MonkeyPatch) -> None:
    result = build_result(large_source(files=0, content_files=60))
    contents = result["evidence"]["contents"]
    included = contents["included"]
    _reduce(result, 40000, monkeypatch)
    contents = result["evidence"]["contents"]
    removed = contents["not_included"]["MAX_ENVELOPE_CHARS"]
    assert removed["total"] == included - contents["included"] > 0
    assert contents["total_chars"] == sum(file["content_chars"] for file in contents["files"])
    assert "MAX_ENVELOPE_CHARS" in contents["truncation"]


def test_the_irreducible_envelope_fits_by_construction() -> None:
    """The worst case of everything that is never reduced fits the budget."""
    result = build_result(large_source(files=0, content_files=0))
    for _, path, field, cut in budget.SECTIONS:
        parent = result["evidence"]
        for key in path[:-1]:
            parent = parent[key]
        if parent[path[-1]][field]:
            parent[path[-1]] = cut(parent[path[-1]], 0)
    owner = "o" * MAX_OWNER_CHARS
    name = "r" * MAX_REPO_NAME_CHARS
    result["repository"].update(
        name_with_owner=f"{owner}/{name}",
        node_id="MDEwOlJlcG9zaXRvcnk" + "A" * 96 + "==",
        url=f"https://github.com/{owner}/{name}",
        default_branch="b" * MAX_BRANCH_NAME_CHARS,
        disk_usage_kib=2**63 - 1,
        viewer_permission="maintain",
        visibility="internal",
    )
    result["acquisition"].update(commit="f" * 64, reference="refs/heads/" + "b" * 255)
    collector = result["collector"]
    collector["limits_reached"] = [name for name, _ in collectors.LIMITS]
    collector["reductions"] = [name for name, *_ in budget.SECTIONS]
    collector["stale_workspaces"] = 2**31
    names = ["w" * NAME_LENGTH] * MAX_SWEEP_ENTRIES
    warnings = [collect.stale_warning(names * 5)]
    text = _serialize_unchecked(
        success(collect.COMMAND, result, warnings=warnings, untrusted=collect.UNTRUSTED)
    )
    assert len(text) < MAX_ENVELOPE_CHARS // 4
    assert max(len(line) for line in text.split("\n")) <= MAX_LINE_CHARS


def test_documented_reduction_order_equals_the_sections() -> None:
    from pathlib import Path

    doc = Path(__file__).resolve().parents[1] / "docs" / "python-package.md"
    text = doc.read_text(encoding="utf-8").split("\n#### Budget reduction\n", 1)[1]
    text = text.split("\nRules:", 1)[0]
    documented = [
        line.split("`")[1] for line in text.splitlines() if line[:1].isdigit() and "`" in line
    ]
    assert documented == [name for name, *_ in budget.SECTIONS]

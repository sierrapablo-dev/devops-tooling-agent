"""Evidence patterns and classification (ADR-0006 Section 11).

The sensitive file names and excluded directories of inspection-guide.md must
equal the package constants until #20 replaces the guide's lists, and the
sensitive names must cover the project secrets of security policy Section 8.3.
"""

import re
from pathlib import Path

import pytest

from devops_tooling_agent import classify
from devops_tooling_agent.classify import Classification
from devops_tooling_agent.classify import classify as classify_path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / ".claude" / "skills" / "analyze-repository" / "inspection-guide.md"
POLICY = ROOT / "docs" / "security-policy.md"
PACKAGE_DOC = ROOT / "docs" / "python-package.md"

_TICKS = re.compile(r"`([^`]+)`")
_PARENTHESES = re.compile(r"\([^)]*\)")


def _section(text: str, heading: str) -> str:
    return text.split(f"\n{heading}\n", 1)[1].split("\n## ", 1)[0]


def guide_excluded_directories() -> list[str]:
    section = _section(GUIDE.read_text(encoding="utf-8"), "## Excluded directories")
    line = next(line for line in section.splitlines() if line.startswith("`"))
    return _TICKS.findall(line)


def guide_sensitive_patterns() -> list[str]:
    section = _section(GUIDE.read_text(encoding="utf-8"), "## Sensitive files")
    patterns: list[str] = []
    for line in section.split("Rules:", 1)[0].splitlines():
        cells = line.split("|")
        if len(cells) < 4 or set(cells[2].strip()) <= {"-"} or "Names" in cells[2]:
            continue
        # Parentheses hold examples, such as `.env.example`, not patterns.
        patterns += _TICKS.findall(_PARENTHESES.sub("", cells[2]))
    return patterns


def guide_agent_files() -> list[str]:
    section = _section(GUIDE.read_text(encoding="utf-8"), "## Agent instruction files")
    line = next(line for line in section.splitlines() if line.startswith("Record the presence"))
    return _TICKS.findall(line)


def policy_project_secrets() -> list[str]:
    text = POLICY.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| Project secrets"))
    return _TICKS.findall(row.split("|")[2])


def package_doc_lists() -> dict[str, list[str]]:
    text = PACKAGE_DOC.read_text(encoding="utf-8")
    table = text.split("\n#### Evidence patterns\n", 1)[1].split("\n#### ", 1)[0]
    lists: dict[str, list[str]] = {}
    for line in table.splitlines():
        cells = line.split("|")
        if len(cells) == 4 and _TICKS.search(cells[2]):
            lists[cells[1].strip().strip("`")] = _TICKS.findall(cells[2])
    return lists


# -- equality with the guide, the policy, and the package documentation ---------------


def test_guide_sensitive_patterns_equal_the_package_constants() -> None:
    assert guide_sensitive_patterns() == list(classify.SENSITIVE_PATTERNS)


def test_guide_excluded_directories_equal_the_package_constants() -> None:
    assert guide_excluded_directories() == list(classify.EXCLUDED_DIRECTORIES)


def test_guide_agent_files_are_agent_components() -> None:
    guide = guide_agent_files()
    assert guide and set(guide) <= set(classify.AGENT_COMPONENT_PATTERNS)
    # The issue #18 list.
    assert {"CLAUDE.md", "AGENTS.md", ".claude/", ".mcp.json"} <= set(
        classify.AGENT_COMPONENT_PATTERNS
    )


def test_sensitive_patterns_cover_the_policy_project_secrets() -> None:
    secrets = policy_project_secrets()
    assert ".env" in secrets
    for pattern in secrets:
        # .terraform/** is a directory: the package names the directory itself.
        expected = pattern.removesuffix("**")
        assert expected in classify.SENSITIVE_PATTERNS, pattern


def test_package_documentation_lists_equal_the_constants() -> None:
    lists = package_doc_lists()
    expected = {
        "Sensitive files": list(classify.SENSITIVE_PATTERNS),
        "Agent components": list(classify.AGENT_COMPONENT_PATTERNS),
        "Excluded directories": list(classify.EXCLUDED_DIRECTORIES),
        **{category: list(patterns) for category, patterns in classify.ALLOWLIST},
    }
    assert lists == expected


def test_unanchored_allowlist_patterns_are_file_names() -> None:
    for _, patterns in classify.ALLOWLIST:
        for pattern in patterns:
            assert pattern.startswith("/") or "/" not in pattern, pattern


# -- matching semantics ------------------------------------------------------------------


def _sample(pattern: str) -> bytes:
    body = pattern.strip("/").replace("*", "x")
    return (body + "/inner.txt" if pattern.endswith("/") else body).encode("ascii")


@pytest.mark.parametrize("pattern", classify.SENSITIVE_PATTERNS)
def test_every_sensitive_pattern_matches_at_any_depth_and_case(pattern: str) -> None:
    for path in (_sample(pattern), b"deep/er/" + _sample(pattern), _sample(pattern).upper()):
        assert classify_path(path).entry_class == "sensitive", path


@pytest.mark.parametrize("pattern", classify.AGENT_COMPONENT_PATTERNS)
def test_every_agent_pattern_matches_at_any_depth_and_case(pattern: str) -> None:
    for path in (_sample(pattern), b"pkg/" + _sample(pattern), _sample(pattern).lower()):
        assert classify_path(path).entry_class == "agent", path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (b".env", Classification("sensitive")),
        (b"config/.ENV.Production", Classification("sensitive")),
        (b".env.example", Classification("sensitive")),
        (b"keys/server.pem", Classification("sensitive")),
        # Trailing dots and spaces fail toward sensitive.
        (b"server.pem. ", Classification("sensitive")),
        (b".env.", Classification("sensitive")),
        # A name pattern on a directory component classifies the directory.
        (b"secrets.d/app.yaml", Classification("sensitive", b"secrets.d")),
        (b"a/credentials/token", Classification("sensitive", b"a/credentials")),
        (b".terraform/providers/x", Classification("sensitive", b".terraform")),
        (b"home/.docker/config.json", Classification("sensitive")),
        (b".docker/other.json", Classification("regular")),
        # Precedence: sensitive, agent, excluded, allowlist.
        (b"node_modules/pkg/.env", Classification("sensitive")),
        (b".claude/.credentials.json", Classification("agent", b".claude")),
        (b".claude/settings.json", Classification("agent", b".claude")),
        (b".claude/skills/x/SKILL.md", Classification("agent", b".claude")),
        (b".github/instructions/a.md", Classification("agent", b".github/instructions")),
        (b"a/.cursor/rules/r.mdc", Classification("agent", b"a/.cursor/rules")),
        (b".cursor/other.json", Classification("regular")),
        (b"pkg/CLAUDE.md", Classification("agent")),
        (b".github/copilot-instructions.md", Classification("agent")),
        (b".mcp.json", Classification("agent")),
        (b"node_modules/pkg/CLAUDE.md", Classification("agent")),
        (b"node_modules/pkg/package.json", Classification("excluded", b"node_modules")),
        (b"src/Build/x.py", Classification("excluded", b"src/Build")),
        (b"src/build/x.py", Classification("excluded", b"src/build")),
        # An excluded name is never the entry itself.
        (b"bin", Classification("regular")),
        (b"tools/build", Classification("regular")),
        # Allowlist.
        (b"package.json", Classification("regular", category="manifest")),
        (b"apps/web/package.json", Classification("regular", category="manifest")),
        (b"PACKAGE.JSON", Classification("regular", category="manifest")),
        (b".github/workflows/ci.yml", Classification("regular", category="ci")),
        # Root-anchored patterns match only at the root.
        (b"sub/.github/workflows/ci.yml", Classification("regular")),
        (b".github/workflows/nested/ci.yml", Classification("regular")),
        (b"infra/main.tf", Classification("regular", category="infrastructure")),
        (b"infra/prod.tfvars", Classification("sensitive")),
        (b"Pulumi.yaml", Classification("regular", category="infrastructure")),
        (b"Pulumi.dev.yaml", Classification("sensitive")),
        (b"Dockerfile.prod", Classification("regular", category="container")),
        (b"docs/README.md", Classification("regular", category="documentation")),
        (b"package-lock.json", Classification("regular")),
        (b".gitmodules", Classification("regular")),
        (b"LICENSE", Classification("regular")),
        # Non-ASCII bytes never match ASCII patterns by case folding.
        (b"\xc3\x89NV", Classification("regular")),
    ],
)
def test_classification(path: bytes, expected: Classification) -> None:
    assert classify_path(path) == expected


def test_matching_is_bytewise_for_non_utf8_paths() -> None:
    assert classify_path(b"\xff\xfe/.env").entry_class == "sensitive"
    assert classify_path(b"\xff/package.json").category == "manifest"


def test_invalid_patterns_are_refused() -> None:
    for pattern in ("", "/", "a//b", "café"):
        with pytest.raises(ValueError):
            classify.compile_patterns([pattern])

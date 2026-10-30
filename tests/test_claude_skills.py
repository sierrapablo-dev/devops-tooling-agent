"""Static invariants of the project skills in .claude/skills/.

These tests check the skill files only. They enforce the skill conventions in
docs/claude-code-environment.md and do not prove runtime behavior.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / ".claude" / "skills"
SKILL_DIRS = sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir())

NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME_LENGTH = 64
FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---\r?\n", re.DOTALL)
KEY_PATTERN = re.compile(r"^(?P<key>[A-Za-z][\w-]*):(?P<value>.*)$")
# Inline shell execution: !`command` and fenced ```! blocks.
INLINE_SHELL_PATTERNS = (
    re.compile(r"!`"),
    re.compile(r"^\s*(```|~~~)!", re.MULTILINE),
)
MARKDOWN_LINK_PATTERN = re.compile(r"\]\((?P<target>[^)\s]+)\)")


def _frontmatter(skill_dir: Path) -> dict[str, str]:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    match = FRONTMATTER_PATTERN.match(text)
    assert match, f"{skill_dir.name}: SKILL.md has no frontmatter"
    fields: dict[str, str] = {}
    key = None
    for line in match["body"].splitlines():
        key_match = KEY_PATTERN.match(line)
        if key_match:
            key = key_match["key"]
            assert key not in fields, f"{skill_dir.name}: duplicate key {key}"
            fields[key] = key_match["value"].strip()
        elif line.strip() and key is not None:
            fields[key] = f"{fields[key]} {line.strip()}".strip()
    return {key: value.strip("\"'") for key, value in fields.items()}


def _markdown_files(skill_dir: Path) -> list[Path]:
    return sorted(skill_dir.rglob("*.md"))


def test_skills_directory_contains_skills() -> None:
    assert SKILL_DIRS


def test_skills_directory_contains_only_skill_directories() -> None:
    assert all(path.is_dir() for path in SKILLS_DIR.iterdir())


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_has_definition(skill_dir: Path) -> None:
    assert (skill_dir / "SKILL.md").is_file()


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_name_matches_directory(skill_dir: Path) -> None:
    name = _frontmatter(skill_dir).get("name")
    assert name == skill_dir.name


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_name_is_kebab_case(skill_dir: Path) -> None:
    assert NAME_PATTERN.match(skill_dir.name)
    assert len(skill_dir.name) <= MAX_NAME_LENGTH


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_has_description(skill_dir: Path) -> None:
    assert _frontmatter(skill_dir).get("description")


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_does_not_preapprove_tools(skill_dir: Path) -> None:
    assert "allowed-tools" not in _frontmatter(skill_dir)


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_has_no_inline_shell_execution(skill_dir: Path) -> None:
    for path in _markdown_files(skill_dir):
        text = path.read_text(encoding="utf-8")
        for pattern in INLINE_SHELL_PATTERNS:
            assert not pattern.search(text), f"{path.relative_to(ROOT)}: {pattern.pattern}"


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda path: path.name)
def test_skill_relative_links_resolve(skill_dir: Path) -> None:
    for path in _markdown_files(skill_dir):
        for match in MARKDOWN_LINK_PATTERN.finditer(path.read_text(encoding="utf-8")):
            target = match["target"].split("#", 1)[0]
            if not target or "://" in target:
                continue
            assert (path.parent / target).exists(), f"{path.relative_to(ROOT)}: {target}"

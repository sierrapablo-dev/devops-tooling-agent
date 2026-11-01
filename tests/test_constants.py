"""docs/python-package.md and the package constants are kept equal.

Every constant named in the operating value tables of Section 4 is either
defined in ``devops_tooling_agent.constants`` with the documented value, or
listed in ``PENDING`` with the issue that introduces it. Every defined constant
is documented.
"""

import ast
import re
from pathlib import Path

from devops_tooling_agent import constants
from devops_tooling_agent.errors import ErrorCode

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "python-package.md"

PENDING = {
    "RECENT_COMMITS": "#18",
    "MAX_TREE_ENTRIES": "#18",
    "MAX_INVENTORY_PATHS": "#18",
    "MAX_CONTENT_FILES": "#18",
    "MAX_FILE_BYTES": "#18",
    "MAX_FILE_LINES": "#18",
    "MAX_TOTAL_CONTENT_CHARS": "#18",
    "MAX_BLOB_BYTES": "#18",
    "BINARY_SNIFF_BYTES": "#18",
    "MAX_COMMIT_SUBJECT_CHARS": "#18",
}

_ROW = re.compile(r"^\| `([A-Z][A-Z0-9_]+)` \| `([^`]+)` \|")
_TABLE_ROW = re.compile(r"^\| `([A-Z_]+)` \|")
_KEY = re.compile(r"`([a-z_]+)`(?::|$)")


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def documented_constants() -> dict[str, object]:
    text = DOC.read_text(encoding="utf-8")
    section = _section(text, "\n## 4. Operating values\n", "\n## ")
    values: dict[str, object] = {}
    for line in section.splitlines():
        match = _ROW.match(line)
        if match:
            name, raw = match.groups()
            assert name not in values, name
            values[name] = _value(raw)
    return values


def _value(raw: str) -> object:
    """Integers and decimals are numbers; anything else, such as ``2.40.0``, is text."""
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
        return ast.literal_eval(raw)
    return raw


def defined_constants() -> dict[str, object]:
    return {name: value for name, value in vars(constants).items() if name.isupper()}


def test_defined_constants_are_documented_with_equal_values() -> None:
    documented = documented_constants()
    for name, value in defined_constants().items():
        assert name in documented, name
        assert documented[name] == value, name
        assert type(documented[name]) is type(value), name


def test_documented_constants_are_defined_or_pending() -> None:
    documented = set(documented_constants())
    defined = set(defined_constants())
    assert documented == defined | set(PENDING)
    assert not defined & set(PENDING)


def test_time_invariants() -> None:
    assert constants.GH_CALL_TIMEOUT_SECONDS < constants.RESOLVE_DEADLINE_SECONDS
    # Below the default shell tool timeout of 120 seconds.
    assert constants.RESOLVE_DEADLINE_SECONDS < 120
    # At or below the documented maximum shell tool timeout.
    assert constants.COLLECT_TOOL_TIMEOUT_MS <= 600000
    # The internal deadline is below the requested tool timeout, with room for uv.
    assert constants.COLLECT_DEADLINE_SECONDS * 1000 < constants.COLLECT_TOOL_TIMEOUT_MS
    # The cleanup reserve covers every removal attempt and the termination wait.
    removal_budget = constants.REMOVAL_ATTEMPTS * constants.REMOVAL_RETRY_DELAY_SECONDS
    assert removal_budget + constants.TERMINATION_WAIT_SECONDS < constants.CLEANUP_RESERVE_SECONDS
    assert constants.CLEANUP_RESERVE_SECONDS < constants.COLLECT_DEADLINE_SECONDS
    working_time = constants.COLLECT_DEADLINE_SECONDS - constants.CLEANUP_RESERVE_SECONDS
    assert working_time > constants.GIT_CALL_TIMEOUT_SECONDS
    # A live collection is never eligible for the sweep.
    assert constants.SWEEP_MIN_AGE_SECONDS > 10 * constants.COLLECT_DEADLINE_SECONDS
    assert constants.WORKSPACE_POLL_SECONDS < constants.GIT_CALL_TIMEOUT_SECONDS


def test_workspace_invariants() -> None:
    assert constants.WORKSPACE_SUFFIX_CHARS % 2 == 0
    assert constants.WORKSPACE_SUFFIX_CHARS >= 32  # at least 128 random bits
    assert constants.MARKER_MAX_BYTES >= 120
    assert constants.MAX_WORKSPACE_BYTES <= constants.MAX_REPO_DISK_USAGE_KIB * 1024


def test_output_invariants() -> None:
    # Far below the Read cap of 25,000 tokens per result.
    assert constants.MAX_LINE_CHARS <= 1024
    assert constants.MAX_ENVELOPE_CHARS > 30000


def test_validation_bounds_fit_a_line() -> None:
    longest_valid = len("https://github.com/") + constants.MAX_OWNER_CHARS + 1
    longest_valid += constants.MAX_REPO_NAME_CHARS + len(".git")
    assert longest_valid <= constants.MAX_IDENTIFIER_CHARS
    # A result line is indentation, key, quotes, and value.
    assert constants.MAX_BRANCH_NAME_CHARS + 40 < constants.MAX_LINE_CHARS
    assert constants.MAX_IDENTIFIER_CHARS + 40 < constants.MAX_LINE_CHARS


def test_defined_error_codes_match_the_documented_table() -> None:
    text = DOC.read_text(encoding="utf-8")
    section = _section(text, "\n## 3. Error and warning codes\n", "\nWarning codes:")
    documented: dict[str, list[str]] = {}
    for line in section.splitlines():
        match = _TABLE_ROW.match(line)
        if match:
            # Details cell: "None", or keys separated by ";", each optionally
            # followed by ": <description>".
            cell = line.rsplit("|", 2)[1].strip()
            keys = [] if cell == "None" else [_KEY.match(part.strip()) for part in cell.split(";")]
            assert all(keys), match.group(1)
            documented[match.group(1)] = [key.group(1) for key in keys if key]
    for code in ErrorCode:
        assert code.name in documented, code.name
        assert documented[code.name] == list(code.detail_keys), code.name

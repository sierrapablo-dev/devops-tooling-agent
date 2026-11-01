"""Fixed ``gh`` argument vectors and the classification of their results.

Only the commands permitted by security policy Section 5.7.3, rule 7, are
built here. Every argument vector is fully determined by validated inputs and
constants. Captured output may be inspected to classify a result, but it is
never reported.
"""

import re
from pathlib import Path
from typing import Final

from devops_tooling_agent.constants import GH_MIN_VERSION
from devops_tooling_agent.errors import CommandError, ErrorCode
from devops_tooling_agent.identifier import HOST, Repository
from devops_tooling_agent.runner import RunResult

REPO_VIEW_FIELDS: Final = (
    "defaultBranchRef",
    "diskUsage",
    "id",
    "isArchived",
    "isEmpty",
    "nameWithOwner",
    "url",
    "viewerPermission",
    "visibility",
)

STAGE_VERSION: Final = "gh_version"
STAGE_AUTH_STATUS: Final = "gh_auth_status"
STAGE_REPO_VIEW: Final = "gh_repo_view"

# gh help exit-codes: 4 means that the command requires authentication.
EXIT_AUTH_REQUIRED: Final = 4
# Verified for a nonexistent repository (docs/claude-code-environment.md, 8.1.9).
NOT_FOUND_FRAGMENT: Final = b"Could not resolve to a Repository with the name"

_VERSION_LINE: Final = re.compile(
    rb"\Agh version ([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})(?=[ (\r\n-]|\Z)"
)

Version = tuple[int, int, int]


def version_argv(gh: Path) -> list[str]:
    return [str(gh), "--version"]


def auth_status_argv(gh: Path) -> list[str]:
    return [str(gh), "auth", "status", "--hostname", HOST]


def repo_view_argv(gh: Path, repository: Repository) -> list[str]:
    return [
        str(gh),
        "repo",
        "view",
        f"{HOST}/{repository.full_name}",
        "--json",
        ",".join(REPO_VIEW_FIELDS),
    ]


def parse_version(stdout: bytes) -> Version | None:
    """Parse the first line of ``gh --version``. Returns ``None`` if unrecognized."""
    match = _VERSION_LINE.match(stdout)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def minimum_version() -> Version:
    major, minor, patch = (int(part) for part in GH_MIN_VERSION.split("."))
    return major, minor, patch


def classify_repo_view_failure(result: RunResult) -> CommandError:
    """Classify a failed ``gh repo view``. Never reports the captured output."""
    if result.exit_code == EXIT_AUTH_REQUIRED:
        return CommandError(ErrorCode.AUTH_REQUIRED)
    if NOT_FOUND_FRAGMENT in result.stderr:
        return CommandError(ErrorCode.NOT_FOUND_OR_INACCESSIBLE)
    return CommandError(
        ErrorCode.PROVIDER_ERROR,
        {"stage": STAGE_REPO_VIEW, "exit_code": result.exit_code},
    )

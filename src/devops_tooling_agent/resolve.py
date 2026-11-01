"""``repo resolve``: validate an identifier and resolve its canonical identity.

Every provider value is validated against a strict grammar or mapped through a
fixed vocabulary before it is emitted, because ``untrusted`` is empty for this
command (docs/python-package.md, Section 2.1).
"""

import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Final

from devops_tooling_agent import gh
from devops_tooling_agent.constants import (
    GH_CALL_TIMEOUT_SECONDS,
    GH_MAX_OUTPUT_BYTES,
    GH_MIN_VERSION,
    MAX_BRANCH_NAME_CHARS,
    RESOLVE_DEADLINE_SECONDS,
)
from devops_tooling_agent.environment import build_environment, resolve_executable
from devops_tooling_agent.errors import CommandError, ErrorCode
from devops_tooling_agent.identifier import (
    HOST,
    IdentifierRejected,
    parse_identifier,
    parse_name_with_owner,
)
from devops_tooling_agent.interrupts import Progress
from devops_tooling_agent.runner import Runner, RunResult

COMMAND: Final = "repo resolve"
STAGE_VALIDATION: Final = "validation"
STAGE_ENVIRONMENT: Final = "environment"

_NODE_ID: Final = re.compile(
    r"(?:R_[A-Za-z0-9_-]{1,96}|MDEwOlJlcG9zaXRvcnk[A-Za-z0-9+/]{1,96}={0,2})", re.ASCII
)
_BRANCH_COMPONENT: Final = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", re.ASCII)
_VISIBILITY: Final = {"PUBLIC": "public", "PRIVATE": "private", "INTERNAL": "internal"}
_PERMISSION: Final = {
    "ADMIN": "admin",
    "MAINTAIN": "maintain",
    "WRITE": "write",
    "TRIAGE": "triage",
    "READ": "read",
}
_MAX_DISK_USAGE: Final = 2**63 - 1


def valid_branch_name(name: str) -> bool:
    """A strict subset of Git branch names.

    Components of letters, digits, ``.``, ``_``, and ``-`` separated by single
    ``/``. No component starts with ``.``, the name does not start with ``-``,
    and it contains no ``..``, does not end with ``.`` or ``.lock``, and is not
    ``HEAD``.
    """
    if not 0 < len(name) <= MAX_BRANCH_NAME_CHARS or name == "HEAD":
        return False
    if ".." in name or name.endswith(".") or name.startswith("-"):
        return False
    for component in name.split("/"):
        if not _BRANCH_COMPONENT.fullmatch(component) or component.endswith(".lock"):
            return False
    return True


def _provider_error() -> CommandError:
    # gh exited successfully, but its output is not consistent with the contract.
    return CommandError(ErrorCode.PROVIDER_ERROR, {"stage": gh.STAGE_REPO_VIEW, "exit_code": 0})


def _default_branch(raw: Any, empty: bool) -> str | None:
    """Apply the default branch matrix of docs/python-package.md, Section 2.1."""
    if raw is None:
        name = ""
    elif isinstance(raw, dict) and set(raw) == {"name"} and isinstance(raw["name"], str):
        name = raw["name"]
    else:
        raise _provider_error()
    if empty:
        if name:
            raise _provider_error()
        return None
    if not name:
        raise _provider_error()
    if not valid_branch_name(name):
        raise CommandError(ErrorCode.UNSUPPORTED_REPOSITORY, {"reason": "default_branch_name"})
    return name


def _viewer_permission(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str) and raw in _PERMISSION:
        return _PERMISSION[raw]
    raise _provider_error()


def parse_repo_view(result: RunResult) -> dict[str, Any]:
    """Validate the JSON of a successful ``gh repo view``."""
    if result.stdout_truncated:
        raise _provider_error()
    try:
        data = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise _provider_error() from None
    if not isinstance(data, dict) or set(data) != set(gh.REPO_VIEW_FIELDS):
        raise _provider_error()

    name_with_owner = data["nameWithOwner"]
    if not isinstance(name_with_owner, str):
        raise _provider_error()
    canonical = parse_name_with_owner(name_with_owner)
    if canonical is None:
        raise _provider_error()
    if data["url"] != f"https://{HOST}/{canonical.full_name}":
        raise _provider_error()
    node_id = data["id"]
    if not isinstance(node_id, str) or not _NODE_ID.fullmatch(node_id):
        raise _provider_error()
    visibility = data["visibility"]
    if not isinstance(visibility, str) or visibility not in _VISIBILITY:
        raise _provider_error()
    archived = data["isArchived"]
    empty = data["isEmpty"]
    if not isinstance(archived, bool) or not isinstance(empty, bool):
        raise _provider_error()
    disk_usage = data["diskUsage"]
    if (
        not isinstance(disk_usage, int)
        or isinstance(disk_usage, bool)
        or not 0 <= disk_usage <= _MAX_DISK_USAGE
    ):
        raise _provider_error()

    return {
        "name_with_owner": canonical.full_name,
        "node_id": node_id,
        "url": f"https://{HOST}/{canonical.full_name}",
        "visibility": _VISIBILITY[visibility],
        "default_branch": _default_branch(data["defaultBranchRef"], empty),
        "viewer_permission": _viewer_permission(data["viewerPermission"]),
        "archived": archived,
        "empty": empty,
        "disk_usage_kib": disk_usage,
    }


def _environment_error(reason: str) -> CommandError:
    return CommandError(
        ErrorCode.ENVIRONMENT_ERROR,
        {"reason": reason, "tool": "gh", "minimum_version": GH_MIN_VERSION},
    )


def resolve(
    identifier: str,
    *,
    runner: Runner,
    environ: Mapping[str, str],
    windows: bool,
    cwd: str,
    clock: Callable[[], float],
    progress: Progress,
) -> dict[str, Any]:
    """Run ``repo resolve`` and return its ``result``. Raises ``CommandError``."""
    deadline = clock() + RESOLVE_DEADLINE_SECONDS

    progress.stage = STAGE_VALIDATION
    try:
        repository = parse_identifier(identifier)
    except IdentifierRejected as rejected:
        raise CommandError(ErrorCode.INVALID_IDENTIFIER, {"reason": rejected.reason}) from None

    progress.stage = STAGE_ENVIRONMENT
    env = build_environment(environ, windows=windows)
    executable = resolve_executable("gh", env, cwd=cwd, windows=windows)
    if executable is None:
        raise _environment_error("not_found")
    # A neutral working directory: it exists, it is not a repository, and it is
    # never inside the agent environment or target data.
    workdir: Path = executable.parent

    def call(stage: str, argv: list[str]) -> RunResult:
        progress.stage = stage
        remaining = deadline - clock()
        if remaining <= 0:
            raise CommandError(ErrorCode.TIMEOUT, {"stage": stage})
        result = runner(
            argv,
            env=env,
            cwd=workdir,
            timeout=min(GH_CALL_TIMEOUT_SECONDS, remaining),
            max_output_bytes=GH_MAX_OUTPUT_BYTES,
        )
        if result.timed_out:
            raise CommandError(ErrorCode.TIMEOUT, {"stage": stage})
        return result

    version_result = call(gh.STAGE_VERSION, gh.version_argv(executable))
    version = gh.parse_version(version_result.stdout) if version_result.exit_code == 0 else None
    if version is None:
        raise _environment_error("version_unknown")
    if version < gh.minimum_version():
        raise _environment_error("version_unsupported")

    # Only the exit status of gh auth status is used (ADR-0006 Section 4).
    auth_result = call(gh.STAGE_AUTH_STATUS, gh.auth_status_argv(executable))
    if auth_result.exit_code != 0:
        raise CommandError(ErrorCode.AUTH_REQUIRED)

    view_result = call(gh.STAGE_REPO_VIEW, gh.repo_view_argv(executable, repository))
    if view_result.exit_code != 0:
        raise gh.classify_repo_view_failure(view_result)
    identity = parse_repo_view(view_result)

    return {
        "input": repository.full_name,
        **identity,
        "input_matches_canonical": repository.full_name == identity["name_with_owner"],
    }

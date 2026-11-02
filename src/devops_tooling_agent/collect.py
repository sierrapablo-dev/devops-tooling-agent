"""``repo collect``: acquire a confirmed repository and collect bounded evidence.

The lifecycle follows ADR-0006 Section 3 in one process:

1. Validate the arguments offline, build the subprocess environment, and
   resolve ``gh`` and ``git``.
2. Check ``gh`` and its authentication, resolve the repository, and verify the
   binding to the confirmed node ID and canonical name, before any workspace
   or Git command exists.
3. Detect stale workspaces of other invocations and report them as a warning,
   without removing them.
4. to 6. Create the workspace, check isolation, and acquire the repository
   (``acquire.Acquisition``).
7. Verify the binding again. A difference discards the evidence.
8. Collect evidence through the source-neutral tree.
9. Remove the workspace (leaving ``Acquisition``).
10. Fit the result to the envelope budget; the caller emits it.

Every ``gh`` and Git call shares the ``COLLECT_DEADLINE_SECONDS`` deadline and
stops at the cleanup reserve. Expiry during collection is a ``TIMEOUT``, never
partial evidence.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any, Final

from devops_tooling_agent import budget, collectors, git, resolve, workspace
from devops_tooling_agent.acquire import Acquisition, Deadline
from devops_tooling_agent.constants import COLLECT_DEADLINE_SECONDS, MAX_SWEEP_ENTRIES
from devops_tooling_agent.envelope import serialized_length, success
from devops_tooling_agent.environment import build_environment
from devops_tooling_agent.errors import CommandError, ErrorCode, WarningCode, warning
from devops_tooling_agent.identifier import IdentifierRejected, Repository, parse_canonical
from devops_tooling_agent.interrupts import Progress
from devops_tooling_agent.runner import Runner
from devops_tooling_agent.tree import GitTreeSource

COMMAND: Final = "repo collect"
UNTRUSTED: Final = ("result.evidence",)
REPO_ID_OPTION: Final = "--repo-id"

STAGE_VALIDATION: Final = "validation"
STAGE_ENVIRONMENT: Final = "environment"
STAGE_STALE_DETECTION: Final = "stale_detection"
STAGE_COLLECTION: Final = "collection"
STAGE_OUTPUT: Final = "output"
BEFORE_ACQUISITION: Final = "before_acquisition"
AFTER_ACQUISITION: Final = "after_acquisition"

# Fields of repo resolve that the repository section reports.
REPOSITORY_FIELDS: Final = (
    "name_with_owner",
    "node_id",
    "url",
    "visibility",
    "default_branch",
    "viewer_permission",
    "archived",
    "empty",
    "disk_usage_kib",
)


def validate(identifier: str, repo_id: str) -> Repository:
    """Offline validation. A rejected value is never reported."""
    try:
        repository = parse_canonical(identifier)
    except IdentifierRejected as rejected:
        raise CommandError(ErrorCode.INVALID_IDENTIFIER, {"reason": rejected.reason}) from None
    if not resolve.valid_node_id(repo_id):
        raise CommandError(
            ErrorCode.INVALID_ARGUMENT, {"argument": REPO_ID_OPTION, "reason": "format"}
        )
    return repository


def _check_binding(
    identity: Mapping[str, Any], repository: Repository, repo_id: str, stage: str
) -> None:
    # Exact, case-sensitive comparison with the confirmed identity.
    if identity["node_id"] != repo_id or identity["name_with_owner"] != repository.full_name:
        raise CommandError(ErrorCode.REPOSITORY_ID_MISMATCH, {"stage": stage})


def stale_warning(names: list[str]) -> dict[str, object]:
    return warning(
        WarningCode.STALE_WORKSPACES_DETECTED,
        {"count": len(names), "names": names[:MAX_SWEEP_ENTRIES]},
    )


def collector_section(reached: collectors.Reached, stale: int) -> dict[str, Any]:
    return {
        "limits": dict(collectors.LIMITS),
        "limits_reached": collectors.ordered(reached.names),
        "reductions": [],
        "stale_workspaces": stale,
        # The workspace name is random, so it is not reported on success
        # (ADR-0007 Section 8). A failed removal is CLEANUP_FAILED instead.
        "cleanup": "removed",
    }


def fit_to_budget(result: dict[str, Any], warnings: list[dict[str, object]]) -> dict[str, Any]:
    """Reduce the evidence until the whole envelope fits ``MAX_ENVELOPE_CHARS``."""

    def measure(candidate: dict[str, Any]) -> int:
        return serialized_length(
            success(COMMAND, candidate, warnings=warnings, untrusted=UNTRUSTED)
        )

    budget.reduce(result, measure)
    return result


def collect(
    identifier: str,
    repo_id: str,
    *,
    runner: Runner,
    environ: Mapping[str, str],
    windows: bool,
    cwd: str,
    clock: Callable[[], float],
    progress: Progress,
    warnings: list[dict[str, object]],
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Run ``repo collect`` and return its ``result``. Raises ``CommandError``.

    ``warnings`` receives the warnings as they occur, so that an error envelope
    reports them too. ``source_url`` is the internal test seam of
    ``acquire.Acquisition``; the command-line interface never sets it.
    """
    deadline = Deadline.start(clock, COLLECT_DEADLINE_SECONDS)

    progress.stage = STAGE_VALIDATION
    repository = validate(identifier, repo_id)

    progress.stage = STAGE_ENVIRONMENT
    env = build_environment(environ, windows=windows)
    gh_path = resolve.resolve_gh(env, cwd=cwd, windows=windows)
    git_path = git.resolve_git(env, cwd=cwd, windows=windows)
    call = resolve.gh_caller(runner, env, gh_path.parent, progress, deadline.working_time)

    resolve.check_gh(gh_path, call)
    identity = resolve.view(gh_path, repository, call)
    _check_binding(identity, repository, repo_id, BEFORE_ACQUISITION)

    progress.stage = STAGE_STALE_DETECTION
    root = workspace.temporary_root(environ, windows=windows, cwd=cwd)
    stale = workspace.detect_stale(root)
    if stale:
        warnings.append(stale_warning(stale))

    acquisition = Acquisition(
        repository=repository,
        default_branch=identity["default_branch"],
        disk_usage_kib=identity["disk_usage_kib"],
        git_path=git_path,
        gh_path=gh_path,
        environ=environ,
        windows=windows,
        cwd=cwd,
        runner=runner,
        deadline=deadline,
        progress=progress,
        wall_clock=wall_clock,
        sleep=sleep,
        source_url=source_url,
    )
    reached = collectors.Reached()
    with acquisition as acquired:
        again = resolve.view(gh_path, repository, call)
        _check_binding(again, repository, repo_id, AFTER_ACQUISITION)
        shallow = collectors.is_shallow(acquired)
        evidence = collectors.evidence(GitTreeSource(acquired), acquired, reached)
        progress.stage = STAGE_COLLECTION
        if deadline.working_time() <= 0:
            raise CommandError(ErrorCode.TIMEOUT, {"stage": STAGE_COLLECTION})
        acquisition_section = {
            "commit": acquired.commit,
            "reference": acquired.reference,
            "depth": acquired.depth,
            "shallow": shallow,
        }

    # The workspace is removed. Only now is the result assembled and fitted.
    progress.stage = STAGE_OUTPUT
    result = {
        "repository": {key: identity[key] for key in REPOSITORY_FIELDS},
        "acquisition": acquisition_section,
        "collector": collector_section(reached, len(stale)),
        "evidence": evidence,
    }
    return fit_to_budget(result, warnings)

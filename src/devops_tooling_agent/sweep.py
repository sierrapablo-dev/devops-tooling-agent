"""``workspace sweep``: list stale workspaces, or remove exactly one eligible workspace.

Listing is S and removal is X (security policy Section 5.7.1). Nothing here
starts a process. A workspace name is validated before any filesystem access,
and a rejected name is never reported.
"""

from collections.abc import Callable, Mapping
from typing import Any, Final

from devops_tooling_agent import workspace
from devops_tooling_agent.errors import CommandError, ErrorCode
from devops_tooling_agent.interrupts import Progress

COMMAND: Final = "workspace sweep"
STAGE_VALIDATION: Final = "validation"
STAGE_ENVIRONMENT: Final = "environment"
STAGE_LISTING: Final = "listing"
STAGE_REMOVAL: Final = "removal"


def _not_eligible(reason: str) -> CommandError:
    return CommandError(ErrorCode.WORKSPACE_NOT_ELIGIBLE, {"reason": reason})


def sweep(
    remove: str | None,
    *,
    environ: Mapping[str, str],
    windows: bool,
    cwd: str,
    wall_clock: Callable[[], float],
    sleep: Callable[[float], None],
    progress: Progress,
) -> dict[str, Any]:
    """Run ``workspace sweep`` and return its ``result``. Raises ``CommandError``."""
    progress.stage = STAGE_VALIDATION
    if remove is not None and not workspace.valid_name(remove):
        raise _not_eligible(workspace.REASON_NAME)

    progress.stage = STAGE_ENVIRONMENT
    root = workspace.temporary_root(environ, windows=windows, cwd=cwd)

    if remove is None:
        progress.stage = STAGE_LISTING
        entries, total = workspace.list_workspaces(root, wall_clock())
        return {
            "workspaces": [entry.to_result() for entry in entries],
            "total": total,
            "truncated": total > len(entries),
        }

    progress.stage = STAGE_REMOVAL
    entry = workspace.inspect(root, remove, wall_clock())
    if not entry.eligible:
        raise _not_eligible(entry.reason or workspace.REASON_NOT_FOUND)

    def still_eligible() -> bool:
        return workspace.inspect(root, remove, wall_clock()).eligible

    if not workspace.remove(root / remove, validate=still_eligible, sleep=sleep):
        raise CommandError(ErrorCode.CLEANUP_FAILED, {"workspace": remove, "prior_error": None})
    return {"removed": remove}

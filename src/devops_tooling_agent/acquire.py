"""The acquisition lifecycle (ADR-0006 Sections 3, 5, 6, and 8).

``Acquisition`` is an internal context manager. Entering it creates a workspace,
checks configuration isolation, initializes a bare repository with an empty
template, and fetches the default branch through the ``gh`` credential helper.
It yields an ``AcquiredRepository`` with the shared deadline. Leaving it
terminates every descendant process and removes the workspace, and its
``outcome`` reports the cleanup, so that the caller emits the envelope
afterwards.

The caller orchestrates the other steps of ADR-0006 Section 3: resolution and
binding before acquisition, stale workspace detection
(``workspace.detect_stale``), the binding check after the fetch, and evidence
collection, which run inside the ``with`` block.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Final

from devops_tooling_agent import git, interrupts, workspace
from devops_tooling_agent.constants import (
    CLEANUP_RESERVE_SECONDS,
    FETCH_DEPTH,
    GIT_CALL_TIMEOUT_SECONDS,
    GIT_MAX_OUTPUT_BYTES,
    MAX_REPO_DISK_USAGE_KIB,
    MAX_WORKSPACE_BYTES,
    WORKSPACE_POLL_SECONDS,
)
from devops_tooling_agent.environment import build_environment
from devops_tooling_agent.errors import CommandError, ErrorCode, Interrupted
from devops_tooling_agent.identifier import Repository
from devops_tooling_agent.resolve import valid_branch_name
from devops_tooling_agent.runner import DescendantsRemain, Runner, RunResult

STAGE_VALIDATION: Final = "validation"
STAGE_WORKSPACE: Final = "workspace"
STAGE_CLEANUP: Final = "cleanup"


@dataclass(frozen=True)
class Deadline:
    """The internal deadline shared by the whole invocation."""

    clock: Callable[[], float]
    expires_at: float

    @classmethod
    def start(cls, clock: Callable[[], float], seconds: float) -> "Deadline":
        return cls(clock, clock() + seconds)

    def remaining(self) -> float:
        return self.expires_at - self.clock()

    def working_time(self) -> float:
        """Time left for acquisition and collection, before the cleanup reserve."""
        return self.remaining() - CLEANUP_RESERVE_SECONDS


@dataclass(frozen=True)
class Cleanup:
    """The cleanup outcome. ``removed`` is ``False`` until removal succeeds."""

    workspace: str | None
    removed: bool


@dataclass(frozen=True)
class AcquiredRepository:
    """The acquired bare repository, yielded by ``Acquisition``."""

    workspace: str
    git_dir: Path
    reference: str
    commit: str
    depth: int
    deadline: Deadline
    _run: Callable[[str, Sequence[str], int], RunResult] = field(repr=False)

    def read(self, command: str, args: Sequence[str], *, max_output_bytes: int) -> RunResult:
        """Run a Git read command (``rev-parse``, ``ls-tree``, ``cat-file``, ``log``)."""
        return self._run(command, args, max_output_bytes)


def prior_error(error: BaseException | None) -> str | None:
    """The error code that a cleanup failure reports as ``prior_error``."""
    if error is None:
        return None
    if isinstance(error, DescendantsRemain):
        if error.result is not None:
            if error.result.timed_out:
                return ErrorCode.TIMEOUT.name
            if error.result.aborted:
                return ErrorCode.LIMIT_EXCEEDED.name
            return None
        return prior_error(error.__context__)
    if isinstance(error, CommandError):
        return error.code.name
    if isinstance(error, (Interrupted, KeyboardInterrupt)):
        return ErrorCode.INTERRUPTED.name
    return ErrorCode.INTERNAL_ERROR.name


def _limit(limit: str, maximum: int, observed: int) -> CommandError:
    return CommandError(
        ErrorCode.LIMIT_EXCEEDED, {"limit": limit, "maximum": maximum, "observed": observed}
    )


class Acquisition:
    """Acquire a repository into an ephemeral workspace, and always remove it.

    ``source_url`` is an internal parameter for tests with local bare
    repositories (ADR-0007 Section 9). It replaces the canonical URL with a
    ``file://`` URL and enables only that protocol. The command-line interface
    never sets it.
    """

    def __init__(
        self,
        *,
        repository: Repository,
        default_branch: str | None,
        disk_usage_kib: int,
        git_path: Path,
        gh_path: Path,
        environ: Mapping[str, str],
        windows: bool,
        cwd: str,
        runner: Runner,
        deadline: Deadline,
        progress: interrupts.Progress,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        source_url: str | None = None,
    ) -> None:
        self._repository = repository
        self._branch = default_branch
        self._disk_usage = disk_usage_kib
        self._git = git_path
        self._gh = gh_path
        self._environ = environ
        self._windows = windows
        self._cwd = cwd
        self._runner = runner
        self._deadline = deadline
        self._progress = progress
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._options = git.GitOptions(allow_file_protocol=source_url is not None)
        self._url = source_url if source_url is not None else git.canonical_url(repository)
        self._workspace: workspace.Workspace | None = None
        self._env: dict[str, str] = {}
        self.outcome = Cleanup(None, False)

    # -- entering -----------------------------------------------------------

    def __enter__(self) -> AcquiredRepository:
        self._progress.stage = STAGE_VALIDATION
        branch = self._branch
        if branch is None:
            raise CommandError(ErrorCode.UNSUPPORTED_REPOSITORY, {"reason": "empty"})
        if not valid_branch_name(branch):
            raise CommandError(ErrorCode.UNSUPPORTED_REPOSITORY, {"reason": "default_branch_name"})
        if self._disk_usage > MAX_REPO_DISK_USAGE_KIB:
            raise _limit("MAX_REPO_DISK_USAGE_KIB", MAX_REPO_DISK_USAGE_KIB, self._disk_usage)
        helper = None if self._options.allow_file_protocol else git.gh_helper(self._gh)

        self._progress.stage = STAGE_WORKSPACE
        root = workspace.temporary_root(self._environ, windows=self._windows, cwd=self._cwd)
        if self._deadline.working_time() <= 0:
            raise CommandError(ErrorCode.TIMEOUT, {"stage": STAGE_WORKSPACE})
        with interrupts.deferred() as received:
            # A signal between creating the directory and recording it here
            # would otherwise leave a workspace that this invocation never removes.
            self._workspace = workspace.create(root, wall_clock=self._wall_clock)
        try:
            if received:
                raise Interrupted
            return self._acquire(self._workspace, branch, helper)
        except BaseException as error:
            self._finish(error)
            raise

    def _acquire(
        self, owned: workspace.Workspace, branch: str, helper: str | None
    ) -> AcquiredRepository:
        self._env = build_environment(
            self._environ,
            windows=self._windows,
            fixed={
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(owned.global_config),
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0",
            },
        )
        repository = owned.repository

        result = self._call(
            git.STAGE_VERSION, git.build_argv(self._git, "version", options=self._options)
        )
        version = git.parse_version(result.stdout) if result.exit_code == 0 else None
        if version is None:
            raise git.environment_error("version_unknown")
        if version < git.minimum_version():
            raise git.environment_error("version_unsupported")

        self._isolation_check(repository)

        argv = git.build_argv(self._git, "init", options=self._options, git_dir=repository)
        if self._call(git.STAGE_INIT, argv).exit_code != 0:
            raise CommandError(ErrorCode.INTERNAL_ERROR)

        self._fetch(owned, repository, branch, helper)

        args = git.rev_parse_args(branch)
        argv = git.build_argv(
            self._git, "rev-parse", options=self._options, git_dir=repository, read_args=args
        )
        result = self._call(git.STAGE_REV_PARSE, argv)
        commit = git.parse_object_id(result.stdout) if result.exit_code == 0 else None
        if commit is None:
            raise CommandError(
                ErrorCode.PROVIDER_ERROR,
                {"stage": git.STAGE_REV_PARSE, "exit_code": result.exit_code},
            )
        return AcquiredRepository(
            workspace=owned.name,
            git_dir=repository,
            reference=f"refs/heads/{branch}",
            commit=commit,
            depth=FETCH_DEPTH,
            deadline=self._deadline,
            _run=self._read,
        )

    def _call(
        self,
        stage: str,
        argv: list[str],
        *,
        max_output_bytes: int = GIT_MAX_OUTPUT_BYTES,
        long_running: bool = False,
        watch: Callable[[], bool] | None = None,
    ) -> RunResult:
        """Run one Git command within the working time. Short commands are also
        bounded by ``GIT_CALL_TIMEOUT_SECONDS``."""
        self._progress.stage = stage
        working = self._deadline.working_time()
        if working <= 0:
            raise CommandError(ErrorCode.TIMEOUT, {"stage": stage})
        timeout = working if long_running else min(GIT_CALL_TIMEOUT_SECONDS, working)
        if self._workspace is None:
            raise RuntimeError("no workspace")
        result = self._runner(
            argv,
            env=self._env,
            # Never inside the repository data (security policy 5.7.3, rule 5).
            cwd=self._workspace.path,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            watch=watch,
            watch_interval=WORKSPACE_POLL_SECONDS,
        )
        if result.timed_out:
            raise CommandError(ErrorCode.TIMEOUT, {"stage": stage})
        return result

    def _isolation_check(self, repository: Path) -> None:
        """Only the command-line options are in effect, and the URL is not rewritten.

        Entries from ``GIT_CONFIG_COUNT`` and ``GIT_CONFIG_PARAMETERS`` are also
        reported with the ``command`` scope, so the entries must equal the
        expected options exactly. The repository does not exist yet, so Git
        reads no repository configuration and discovers no other repository.
        """
        argv = git.build_argv(self._git, "config", options=self._options, git_dir=repository)
        result = self._call(git.STAGE_ISOLATION, argv)
        lines = result.stdout.replace(b"\r\n", b"\n").split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        expected = [line.encode("ascii") for line in git.expected_scope_lines(self._options)]
        if result.exit_code != 0 or result.stdout_truncated or lines != expected:
            raise CommandError(ErrorCode.UNSAFE_ENVIRONMENT, {"check": "configuration_scope"})

        argv = git.build_argv(
            self._git, "get-url", options=self._options, git_dir=repository, url=self._url
        )
        result = self._call(git.STAGE_ISOLATION, argv)
        if result.exit_code != 0 or result.stdout.rstrip(b"\r\n") != self._url.encode("ascii"):
            raise CommandError(ErrorCode.UNSAFE_ENVIRONMENT, {"check": "url_rewrite"})

    def _fetch(
        self, owned: workspace.Workspace, repository: Path, branch: str, helper: str | None
    ) -> None:
        observed = 0

        def within_limit() -> bool:
            nonlocal observed
            observed = workspace.size_bytes(owned.path)
            return observed <= MAX_WORKSPACE_BYTES

        argv = git.build_argv(
            self._git,
            "fetch",
            options=self._options,
            git_dir=repository,
            url=self._url,
            branch=branch,
            helper=helper,
        )
        result = self._call(git.STAGE_FETCH, argv, long_running=True, watch=within_limit)
        if result.aborted:
            raise _limit("MAX_WORKSPACE_BYTES", MAX_WORKSPACE_BYTES, observed)
        if result.exit_code != 0:
            if git.FSCK_FRAGMENT in result.stderr:
                raise CommandError(
                    ErrorCode.UNSUPPORTED_REPOSITORY, {"reason": "object_check_failed"}
                )
            raise CommandError(
                ErrorCode.PROVIDER_ERROR, {"stage": git.STAGE_FETCH, "exit_code": result.exit_code}
            )
        # A fetch can finish between two polls.
        if not within_limit():
            raise _limit("MAX_WORKSPACE_BYTES", MAX_WORKSPACE_BYTES, observed)

    def _read(self, command: str, args: Sequence[str], max_output_bytes: int) -> RunResult:
        if command not in git.READ_COMMANDS:
            raise ValueError("not a read command")
        if self._workspace is None:
            raise RuntimeError("no workspace")
        argv = git.build_argv(
            self._git,
            command,
            options=self._options,
            git_dir=self._workspace.repository,
            read_args=args,
        )
        stage = "git_" + command.replace("-", "_")
        return self._call(stage, argv, max_output_bytes=max_output_bytes)

    # -- leaving ------------------------------------------------------------

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._finish(exc)

    def _finish(self, error: BaseException | None) -> None:
        """Remove the workspace. Every descendant has already ended, because the
        runner returns or raises only after confirming it. Raises
        ``CLEANUP_FAILED`` when the workspace remains, which takes precedence
        over ``error``.
        """
        owned = self._workspace
        if owned is None:
            return
        self._workspace = None
        self._progress.stage = STAGE_CLEANUP
        with interrupts.deferred() as received:
            if isinstance(error, DescendantsRemain):
                # Removal never starts while a descendant may still run.
                removed = False
            else:
                removed = workspace.remove(
                    owned.path, validate=lambda: workspace.owned(owned), sleep=self._sleep
                )
        self.outcome = Cleanup(owned.name, removed)
        if not removed:
            raise CommandError(
                ErrorCode.CLEANUP_FAILED,
                {"workspace": owned.name, "prior_error": prior_error(error)},
            ) from None
        if received and error is None:
            raise Interrupted

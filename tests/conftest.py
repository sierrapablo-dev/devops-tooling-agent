"""Shared fakes for package tests.

Tests run without network access, gh, git, or an LLM. Recorded gh results come
from the verification in docs/claude-code-environment.md, Section 8.1.9. They
contain no tokens, account names, or operator paths.
"""

import io
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from devops_tooling_agent.cli import main
from devops_tooling_agent.runner import RunResult

WINDOWS = sys.platform == "win32"

VERSION_OK = b"gh version 2.92.0 (2026-04-28)\nhttps://github.com/cli/cli/releases/tag/v2.92.0\n"
PUBLIC_VIEW = {
    "defaultBranchRef": {"name": "master"},
    "diskUsage": 1,
    "id": "MDEwOlJlcG9zaXRvcnkxMjk2MjY5",
    "isArchived": False,
    "isEmpty": False,
    "nameWithOwner": "octocat/Hello-World",
    "url": "https://github.com/octocat/Hello-World",
    "viewerPermission": "READ",
    "visibility": "PUBLIC",
}
NOT_FOUND_STDERR = (
    b"GraphQL: Could not resolve to a Repository with the name 'octocat/missing-repo'."
    b" (repository)\n"
)
NO_AUTH_STDERR = (
    b"To get started with GitHub CLI, please run:  gh auth login\n"
    b"Alternatively, populate the GH_TOKEN environment variable with a GitHub API"
    b" authentication token.\n"
)
NETWORK_STDERR = (
    b'Post "https://api.github.com/graphql": proxyconnect tcp: dial tcp 127.0.0.1:9:'
    b" connectex: No connection could be made because the target machine actively"
    b" refused it.\n"
)


def result(
    exit_code: int | None = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    *,
    timed_out: bool = False,
    stdout_truncated: bool = False,
) -> RunResult:
    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=False,
    )


def view(**changes: Any) -> RunResult:
    data = dict(PUBLIC_VIEW)
    data.update(changes)
    return result(0, json.dumps(data).encode())


@dataclass
class Call:
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    timeout: float
    max_output_bytes: int


Step = RunResult | BaseException | Callable[[], RunResult]


@dataclass
class FakeRunner:
    """Returns scripted results in order and records every call."""

    steps: list[Step]
    calls: list[Call] = field(default_factory=list)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float,
        max_output_bytes: int,
    ) -> RunResult:
        self.calls.append(Call(list(argv), dict(env), cwd, timeout, max_output_bytes))
        if not self.steps:
            raise AssertionError("unexpected subprocess call")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step()
        return step


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Invocation:
    exit_code: int
    raw: bytes
    runner: FakeRunner

    @property
    def envelope(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.raw.decode("ascii"))
        return loaded


@pytest.fixture
def gh_dir(tmp_path: Path) -> Path:
    """A directory with a placeholder gh executable. It is never executed."""
    directory = tmp_path / "bin"
    directory.mkdir()
    executable = directory / ("gh.exe" if WINDOWS else "gh")
    executable.write_bytes(b"")
    executable.chmod(0o755)
    return directory


@pytest.fixture
def environ(gh_dir: Path) -> dict[str, str]:
    env = {"PATH": str(gh_dir)}
    if WINDOWS:
        env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    return env


InvokeFn = Callable[..., Invocation]


@pytest.fixture
def invoke(environ: dict[str, str], tmp_path: Path) -> InvokeFn:
    def _invoke(
        argv: Sequence[str],
        steps: Sequence[Step] = (),
        *,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> Invocation:
        runner = FakeRunner(list(steps))
        out = io.BytesIO()
        code = main(
            argv,
            runner=runner,
            environ=environ if env is None else env,
            windows=WINDOWS,
            cwd=str(tmp_path),
            clock=FakeClock() if clock is None else clock,
            stdout=out,
        )
        return Invocation(code, out.getvalue(), runner)

    return _invoke


def resolve_steps(*last: Step) -> list[Step]:
    """The version and authentication steps of a successful resolution, then ``last``."""
    return [result(0, VERSION_OK), result(0), *last]

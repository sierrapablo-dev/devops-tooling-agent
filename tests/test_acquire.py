"""The acquisition lifecycle (ADR-0006 Sections 3, 5, 6, and 8).

Tests with real Git use local bare repositories through the internal
``source_url`` parameter. The others use the injectable runner. No network
access, gh, or LLM is involved.
"""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from conftest import FakeClock, FakeRunner, Step, result
from devops_tooling_agent import acquire, git, workspace
from devops_tooling_agent.acquire import Acquisition, Deadline, prior_error
from devops_tooling_agent.constants import (
    CLEANUP_RESERVE_SECONDS,
    COLLECT_DEADLINE_SECONDS,
    FETCH_DEPTH,
    GIT_CALL_TIMEOUT_SECONDS,
    MARKER_FILE_NAME,
)
from devops_tooling_agent.environment import build_environment
from devops_tooling_agent.errors import CommandError, ErrorCode, Interrupted
from devops_tooling_agent.identifier import Repository
from devops_tooling_agent.interrupts import Progress
from devops_tooling_agent.runner import DescendantsRemain, RunResult, run
from gitfixtures import (
    WINDOWS,
    head_commit,
    make_malformed_source,
    make_source,
    marker_configuration,
    requires_git,
)

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = Repository("octocat", "Hello-World")
COMMIT = "a" * 40
OK_CONFIG = ("\n".join(git.expected_scope_lines(git.GitOptions())) + "\n").encode()
OK_URL = b"https://github.com/octocat/Hello-World\n"


class Setup:
    def __init__(self, tmp_path: Path) -> None:
        self.temp_root = tmp_path / "system-temp"
        self.temp_root.mkdir()
        self.agent = tmp_path / "agent"
        self.agent.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.gh = self.bin / ("gh.exe" if WINDOWS else "gh")
        self.gh.write_bytes(b"")
        self.environ: dict[str, str] = {"TMPDIR": str(self.temp_root)}
        for key in ("PATH", "PATHEXT", "SYSTEMROOT"):
            if key in os.environ:
                self.environ[key] = os.environ[key]

    def git_path(self) -> Path:
        env = build_environment(self.environ, windows=WINDOWS)
        return git.resolve_git(env, cwd=str(self.agent), windows=WINDOWS)

    def acquisition(
        self,
        runner: Any,
        *,
        source: Path | None = None,
        source_url: str | None = None,
        branch: str | None = "main",
        disk_usage_kib: int = 1,
        clock: Callable[[], float] | None = None,
        git_path: Path | None = None,
        gh_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
        progress: Progress | None = None,
    ) -> Acquisition:
        if source is not None:
            source_url = source.as_uri()
        deadline_clock = clock if clock is not None else time.monotonic
        return Acquisition(
            repository=REPOSITORY,
            default_branch=branch,
            disk_usage_kib=disk_usage_kib,
            git_path=git_path if git_path is not None else self.bin / "git",
            gh_path=gh_path if gh_path is not None else self.gh,
            environ=self.environ if environ is None else environ,
            windows=WINDOWS,
            cwd=str(self.agent),
            runner=runner,
            deadline=Deadline.start(deadline_clock, COLLECT_DEADLINE_SECONDS),
            progress=progress if progress is not None else Progress("validation"),
            sleep=lambda seconds: None,
            source_url=source_url,
        )

    def leftovers(self) -> list[str]:
        return sorted(path.name for path in self.temp_root.iterdir())


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


def fake_steps(*, fetch: Step | None = None, rev_parse: Step | None = None) -> list[Step]:
    return [
        result(0, b"git version 2.49.0.windows.1\n"),
        result(0, OK_CONFIG),
        result(0, OK_URL),
        result(0),
        fetch if fetch is not None else result(0),
        rev_parse if rev_parse is not None else result(0, COMMIT.encode() + b"\n"),
    ]


def expect_error(code: ErrorCode, details: dict[str, Any], action: Callable[[], Any]) -> None:
    with pytest.raises(CommandError) as raised:
        action()
    assert raised.value.code is code
    assert dict(raised.value.details) == details


def enter(acquisition: Acquisition) -> None:
    with acquisition:
        pass


# -- argument and environment construction (fake runner) ------------------------


def test_git_argument_vectors_and_environment(setup: Setup) -> None:
    runner = FakeRunner(fake_steps())
    acquisition = setup.acquisition(runner)
    with acquisition as repo:
        workspace_path = setup.temp_root / repo.workspace
        assert (workspace_path / MARKER_FILE_NAME).is_file()
    git_path = str(setup.bin / "git")
    hardening = [
        "-c",
        "credential.helper=",
        "-c",
        "core.askPass=",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "-c",
        "transfer.fsckObjects=true",
        "-c",
        "gc.auto=0",
        "-c",
        "maintenance.auto=false",
    ]
    git_dir = workspace_path / "repository.git"
    helper = f"!'{setup.gh.as_posix()}' auth git-credential"
    url = "https://github.com/octocat/Hello-World"
    expected = [
        [git_path, *hardening, "version"],
        [git_path, *hardening, f"--git-dir={git_dir}", "config", "--list", "--show-scope"],
        [git_path, *hardening, f"--git-dir={git_dir}", "ls-remote", "--get-url", url],
        [git_path, *hardening, "init", "--bare", "--template=", "--quiet", str(git_dir)],
        [
            git_path,
            *hardening[:2],
            "-c",
            f"credential.helper={helper}",
            *hardening[2:],
            f"--git-dir={git_dir}",
            "fetch",
            "--no-tags",
            "--no-recurse-submodules",
            "--no-auto-maintenance",
            "--no-write-fetch-head",
            f"--depth={FETCH_DEPTH}",
            "--quiet",
            url,
            "+refs/heads/main:refs/heads/main",
        ],
        [
            git_path,
            *hardening,
            f"--git-dir={git_dir}",
            "rev-parse",
            "--verify",
            "--quiet",
            "--end-of-options",
            "refs/heads/main^{commit}",
        ],
    ]
    assert [call.argv for call in runner.calls] == expected
    for call in runner.calls:
        assert call.cwd == workspace_path
        assert call.env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert call.env["GIT_CONFIG_GLOBAL"] == str(workspace_path / "gitconfig")
        assert call.env["GIT_ATTR_NOSYSTEM"] == "1"
        assert call.env["GIT_NO_REPLACE_OBJECTS"] == "1"
        assert call.env["GIT_OPTIONAL_LOCKS"] == "0"
        assert call.env["GIT_TERMINAL_PROMPT"] == "0"
        assert call.env["GCM_INTERACTIVE"] == "never"
        assert "core.hooksPath" not in " ".join(call.argv)
    assert acquisition.outcome == acquire.Cleanup(repo.workspace, True)
    assert repo.commit == COMMIT
    assert repo.reference == "refs/heads/main"
    assert repo.depth == FETCH_DEPTH
    assert setup.leftovers() == []


def test_inherited_git_variables_are_dropped(setup: Setup) -> None:
    runner = FakeRunner(fake_steps())
    environ = {
        **setup.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": "/x",
        "GIT_CONFIG_PARAMETERS": "'core.hookspath'='/x'",
        "GIT_DIR": "/x",
        "GIT_TEMPLATE_DIR": "/x",
        "GH_TOKEN": "not-a-token",
        "GITHUB_TOKEN": "not-a-token",
    }
    enter(setup.acquisition(runner, environ=environ))
    for call in runner.calls:
        for name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GH_TOKEN"):
            assert name not in call.env
        assert "GIT_TEMPLATE_DIR" not in call.env and "GITHUB_TOKEN" not in call.env


def test_build_argv_refuses_what_rule_8_does_not_permit(tmp_path: Path) -> None:
    git_path = tmp_path / "git"
    options = git.GitOptions()
    with pytest.raises(ValueError):
        git.build_argv(git_path, "push", options=options, git_dir=tmp_path)
    with pytest.raises(ValueError):
        git.build_argv(git_path, "config", options=options, git_dir=tmp_path, helper="!x")
    with pytest.raises(ValueError):
        git.build_argv(
            git_path,
            "fetch",
            options=options,
            git_dir=tmp_path,
            branch="main",
            url="https://example.com/o/r",
            helper="!x",
        )
    with pytest.raises(ValueError):
        git.build_argv(
            git_path,
            "fetch",
            options=options,
            git_dir=tmp_path,
            branch="main",
            url="file:///tmp/x",
            helper="!x",
        )
    with pytest.raises(ValueError):
        git.build_argv(
            git_path,
            "fetch",
            options=options,
            git_dir=tmp_path,
            branch="-x",
            url="https://github.com/o/r",
            helper="!x",
        )
    with pytest.raises(ValueError):
        git.build_argv(
            git_path,
            "fetch",
            options=options,
            git_dir=tmp_path,
            branch="main",
            url="https://github.com/o/r",
        )
    with pytest.raises(ValueError):
        git.build_argv(git_path, "version", options=options, git_dir=tmp_path)
    with pytest.raises(ValueError):
        git.build_argv(git_path, "config", options=options)
    with pytest.raises(ValueError):
        git.build_argv(Path("git"), "version", options=options)
    with pytest.raises(ValueError):
        git.build_argv(git_path, "config", options=options, git_dir=tmp_path, read_args=["x"])
    log = git.build_argv(git_path, "log", options=options, git_dir=tmp_path, read_args=["-n", "1"])
    assert log[-3:] == ["--no-show-signature", "-n", "1"]
    test_only = git.build_argv(
        git_path, "version", options=git.GitOptions(allow_file_protocol=True)
    )
    assert "protocol.file.allow=always" in test_only
    assert "protocol.file.allow=always" not in git.build_argv(git_path, "version", options=options)


@pytest.mark.parametrize(
    ("path", "accepted"),
    [
        ("C:/Program Files/GitHub CLI/gh.exe", True),
        ("/usr/bin/gh", True),
        ("/opt/o'brien/gh", False),
        ("/opt/a\tb/gh", False),
        ("/opt/a\nb/gh", False),
        ("/opt/a\x7fb/gh", False),
    ],
)
def test_gh_helper_quoting(path: str, accepted: bool) -> None:
    if accepted:
        assert git.gh_helper(Path(path)) == f"!'{Path(path).as_posix()}' auth git-credential"
    else:
        expect_error(
            ErrorCode.ENVIRONMENT_ERROR,
            {"reason": "unsupported_path", "tool": "gh", "minimum_version": "2.40.0"},
            lambda: git.gh_helper(Path(path)),
        )


def test_gh_path_that_cannot_be_quoted_creates_nothing(setup: Setup) -> None:
    runner = FakeRunner([])
    acquisition = setup.acquisition(runner, gh_path=setup.bin / "o'brien" / "gh")
    with pytest.raises(CommandError) as raised:
        enter(acquisition)
    assert raised.value.details["reason"] == "unsupported_path"
    assert runner.calls == [] and setup.leftovers() == []


@pytest.mark.parametrize(
    ("stdout", "parsed"),
    [
        (b"git version 2.49.0.windows.1\n", (2, 49, 0)),
        (b"git version 2.39.3 (Apple Git-145)\n", (2, 39, 3)),
        (b"git version 2.43.0\n", (2, 43, 0)),
        (b"git version 2.43\n", None),
        (b"version 2.43.0\n", None),
        (b"", None),
    ],
)
def test_git_version_parsing(stdout: bytes, parsed: tuple[int, int, int] | None) -> None:
    assert git.parse_version(stdout) == parsed


@pytest.mark.parametrize(
    ("version_result", "reason"),
    [
        (result(0, b"git version 2.31.9\n"), "version_unsupported"),
        (result(0, b"nonsense\n"), "version_unknown"),
        (result(1, b"git version 2.49.0\n"), "version_unknown"),
    ],
)
def test_git_version_check(setup: Setup, version_result: RunResult, reason: str) -> None:
    runner = FakeRunner([version_result])
    details = {"reason": reason, "tool": "git", "minimum_version": "2.32.0"}
    expect_error(ErrorCode.ENVIRONMENT_ERROR, details, lambda: enter(setup.acquisition(runner)))
    assert setup.leftovers() == []


def test_git_not_found(tmp_path: Path) -> None:
    expect_error(
        ErrorCode.ENVIRONMENT_ERROR,
        {"reason": "not_found", "tool": "git", "minimum_version": "2.32.0"},
        lambda: git.resolve_git({"PATH": str(tmp_path)}, cwd=str(tmp_path), windows=WINDOWS),
    )


# -- refusals before the workspace exists ---------------------------------------


@pytest.mark.parametrize(
    ("branch", "disk", "code", "details"),
    [
        (None, 1, ErrorCode.UNSUPPORTED_REPOSITORY, {"reason": "empty"}),
        ("bad..name", 1, ErrorCode.UNSUPPORTED_REPOSITORY, {"reason": "default_branch_name"}),
        (
            "main",
            1048577,
            ErrorCode.LIMIT_EXCEEDED,
            {"limit": "MAX_REPO_DISK_USAGE_KIB", "maximum": 1048576, "observed": 1048577},
        ),
    ],
)
def test_refusals_create_no_workspace(
    setup: Setup, branch: str | None, disk: int, code: ErrorCode, details: dict[str, Any]
) -> None:
    runner = FakeRunner([])
    acquisition = setup.acquisition(runner, branch=branch, disk_usage_kib=disk)
    expect_error(code, details, lambda: enter(acquisition))
    assert runner.calls == [] and setup.leftovers() == []
    assert acquisition.outcome == acquire.Cleanup(None, False)


def test_temporary_directory_inside_the_agent_environment(setup: Setup) -> None:
    inside = setup.agent / "tmp"
    inside.mkdir()
    environ = {**setup.environ, "TMPDIR": str(inside)}
    runner = FakeRunner([])
    details = {"reason": "temporary_directory", "tool": None, "minimum_version": None}
    expect_error(
        ErrorCode.ENVIRONMENT_ERROR,
        details,
        lambda: enter(setup.acquisition(runner, environ=environ)),
    )
    assert list(inside.iterdir()) == []


# -- isolation self-check ----------------------------------------------------------


@pytest.mark.parametrize(
    ("config", "url", "check"),
    [
        (result(0, OK_CONFIG + b"global\tcore.hookspath=/x\n"), None, "configuration_scope"),
        (result(0, OK_CONFIG + b"command\tcore.hookspath=/x\n"), None, "configuration_scope"),
        (result(0, b"command\tcredential.helper=\n"), None, "configuration_scope"),
        (result(1, OK_CONFIG), None, "configuration_scope"),
        (result(0, OK_CONFIG, stdout_truncated=True), None, "configuration_scope"),
        (result(0, OK_CONFIG), result(0, b"https://evil.example/o/r\n"), "url_rewrite"),
        (result(0, OK_CONFIG), result(128, OK_URL), "url_rewrite"),
    ],
)
def test_isolation_check_failures(
    setup: Setup, config: RunResult, url: RunResult | None, check: str
) -> None:
    steps: list[Step] = [result(0, b"git version 2.49.0\n"), config]
    if url is not None:
        steps.append(url)
    runner = FakeRunner(steps)
    acquisition = setup.acquisition(runner)
    expect_error(ErrorCode.UNSAFE_ENVIRONMENT, {"check": check}, lambda: enter(acquisition))
    # The self-check runs before the repository is created.
    assert all("init" not in call.argv for call in runner.calls)
    assert setup.leftovers() == []
    assert acquisition.outcome.removed is True


def test_isolation_check_accepts_crlf(setup: Setup) -> None:
    steps = fake_steps()
    steps[1] = result(0, OK_CONFIG.replace(b"\n", b"\r\n"))
    steps[2] = result(0, OK_URL.replace(b"\n", b"\r\n"))
    enter(setup.acquisition(FakeRunner(steps)))


# -- deadline, limits, and failures (fake runner) ------------------------------------


def test_short_commands_and_fetch_use_their_timeouts(setup: Setup) -> None:
    clock = FakeClock()
    runner = FakeRunner(fake_steps())
    enter(setup.acquisition(runner, clock=clock))
    working = COLLECT_DEADLINE_SECONDS - CLEANUP_RESERVE_SECONDS
    timeouts = [call.timeout for call in runner.calls]
    assert timeouts == [GIT_CALL_TIMEOUT_SECONDS] * 4 + [working, GIT_CALL_TIMEOUT_SECONDS]
    fetch = runner.calls[4]
    assert fetch.watch is not None and fetch.watch_interval == 1


def test_fetch_timeout_shrinks_with_the_remaining_time(setup: Setup) -> None:
    clock = FakeClock()

    def advance() -> RunResult:
        clock.now += 100
        return result(0)

    runner = FakeRunner(fake_steps())
    runner.steps[3] = advance  # init takes 100 seconds
    enter(setup.acquisition(runner, clock=clock))
    working = COLLECT_DEADLINE_SECONDS - CLEANUP_RESERVE_SECONDS
    assert runner.calls[4].timeout == working - 100
    assert runner.calls[5].timeout == GIT_CALL_TIMEOUT_SECONDS


@pytest.mark.parametrize("step_index", [0, 1, 3, 4, 5])
def test_deadline_expiry_before_a_stage(setup: Setup, step_index: int) -> None:
    clock = FakeClock()
    stages = [
        "git_version",
        "isolation_check",
        "isolation_check",
        "git_init",
        "git_fetch",
        "git_rev_parse",
    ]
    steps = fake_steps()
    if step_index:

        def expire(previous: Step = steps[step_index - 1]) -> RunResult:
            clock.now += COLLECT_DEADLINE_SECONDS - CLEANUP_RESERVE_SECONDS
            assert isinstance(previous, RunResult)
            return previous

        steps[step_index - 1] = expire
    runner = FakeRunner(steps)
    acquisition = setup.acquisition(runner, clock=clock)
    if not step_index:
        clock.now += COLLECT_DEADLINE_SECONDS  # expired before the workspace exists
    stage = stages[step_index] if step_index else "workspace"
    expect_error(ErrorCode.TIMEOUT, {"stage": stage}, lambda: enter(acquisition))
    assert len(runner.calls) == step_index
    assert setup.leftovers() == []


def test_timed_out_fetch(setup: Setup) -> None:
    runner = FakeRunner(fake_steps(fetch=result(None, timed_out=True)))
    expect_error(
        ErrorCode.TIMEOUT, {"stage": "git_fetch"}, lambda: enter(setup.acquisition(runner))
    )
    assert setup.leftovers() == []


def test_aborted_fetch_is_limit_exceeded(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acquire, "MAX_WORKSPACE_BYTES", 10)
    runner = FakeRunner(fake_steps())

    def aborted() -> RunResult:
        watch = runner.calls[-1].watch
        assert watch is not None and watch() is False  # the marker alone exceeds 10 bytes
        return result(None, aborted=True)

    runner.steps[4] = aborted
    acquisition = setup.acquisition(runner)
    with pytest.raises(CommandError) as raised:
        enter(acquisition)
    assert raised.value.code is ErrorCode.LIMIT_EXCEEDED
    assert raised.value.details["limit"] == "MAX_WORKSPACE_BYTES"
    assert raised.value.details["maximum"] == 10
    observed = raised.value.details["observed"]
    assert isinstance(observed, int) and observed > 10
    assert setup.leftovers() == []


@pytest.mark.parametrize(
    ("fetch", "code", "details"),
    [
        (
            result(128, stderr=b"fatal: fsck error in packed object\n"),
            ErrorCode.UNSUPPORTED_REPOSITORY,
            {"reason": "object_check_failed"},
        ),
        (
            result(128, stderr=b"fatal: repository not found\n"),
            ErrorCode.PROVIDER_ERROR,
            {"stage": "git_fetch", "exit_code": 128},
        ),
    ],
)
def test_fetch_failures(
    setup: Setup, fetch: RunResult, code: ErrorCode, details: dict[str, Any]
) -> None:
    acquisition = setup.acquisition(FakeRunner(fake_steps(fetch=fetch)))
    expect_error(code, details, lambda: enter(acquisition))
    assert setup.leftovers() == []


def test_unexpected_rev_parse_output(setup: Setup) -> None:
    runner = FakeRunner(fake_steps(rev_parse=result(0, b"not an object id\n")))
    expect_error(
        ErrorCode.PROVIDER_ERROR,
        {"stage": "git_rev_parse", "exit_code": 0},
        lambda: enter(setup.acquisition(runner)),
    )
    assert setup.leftovers() == []


# -- cleanup ---------------------------------------------------------------------


def test_descendants_remain_leave_the_workspace(setup: Setup) -> None:
    timed_out = result(None, timed_out=True)
    runner = FakeRunner(fake_steps(fetch=DescendantsRemain(timed_out)))
    acquisition = setup.acquisition(runner)
    with pytest.raises(CommandError) as raised:
        enter(acquisition)
    assert raised.value.code is ErrorCode.CLEANUP_FAILED
    name = raised.value.details["workspace"]
    assert raised.value.details == {"workspace": name, "prior_error": "TIMEOUT"}
    # Removal never started: the workspace stays detectable for workspace sweep.
    assert setup.leftovers() == [name]
    assert workspace.read_marker(setup.temp_root / str(name)) is not None
    assert acquisition.outcome == acquire.Cleanup(str(name), False)


def test_cleanup_failure_takes_precedence(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace, "remove", lambda *args, **kwargs: False)
    fetch = result(128, stderr=b"fatal: unable to access\n")
    acquisition = setup.acquisition(FakeRunner(fake_steps(fetch=fetch)))
    with pytest.raises(CommandError) as raised:
        enter(acquisition)
    assert raised.value.code is ErrorCode.CLEANUP_FAILED
    assert raised.value.details["prior_error"] == "PROVIDER_ERROR"


def test_cleanup_failure_after_success_has_no_prior_error(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace, "remove", lambda *args, **kwargs: False)
    with pytest.raises(CommandError) as raised:
        enter(setup.acquisition(FakeRunner(fake_steps())))
    assert raised.value.code is ErrorCode.CLEANUP_FAILED
    assert raised.value.details["prior_error"] is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, None),
        (CommandError(ErrorCode.TIMEOUT, {"stage": "git_fetch"}), "TIMEOUT"),
        (Interrupted(), "INTERRUPTED"),
        (KeyboardInterrupt(), "INTERRUPTED"),
        (RuntimeError("x"), "INTERNAL_ERROR"),
        (DescendantsRemain(result(None, aborted=True)), "LIMIT_EXCEEDED"),
        (DescendantsRemain(result(0)), None),
    ],
)
def test_prior_error(error: BaseException | None, expected: str | None) -> None:
    assert prior_error(error) == expected


def test_prior_error_of_descendants_remain_uses_the_context() -> None:
    try:
        try:
            raise Interrupted
        except Interrupted:
            raise DescendantsRemain(None)  # noqa: B904
    except DescendantsRemain as error:
        assert prior_error(error) == "INTERRUPTED"


def test_exception_inside_the_block_removes_the_workspace(setup: Setup) -> None:
    acquisition = setup.acquisition(FakeRunner(fake_steps()))
    with pytest.raises(RuntimeError), acquisition:
        raise RuntimeError("collection failed")
    assert acquisition.outcome.removed is True
    assert setup.leftovers() == []


def test_signal_during_cleanup_is_deferred(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    real_remove = workspace.remove

    def remove_with_signal(*args: Any, **kwargs: Any) -> bool:
        signal.raise_signal(signal.SIGINT)
        return real_remove(*args, **kwargs)

    monkeypatch.setattr(workspace, "remove", remove_with_signal)
    progress = Progress("validation")
    previous = signal.signal(signal.SIGINT, lambda signum, frame: pytest.fail("not deferred"))
    try:
        acquisition = setup.acquisition(FakeRunner(fake_steps()), progress=progress)
        with pytest.raises(Interrupted), acquisition:
            pass
    finally:
        signal.signal(signal.SIGINT, previous)
    assert acquisition.outcome.removed is True
    assert progress.stage == "cleanup"
    assert setup.leftovers() == []


def test_read_commands(setup: Setup) -> None:
    runner = FakeRunner([*fake_steps(), result(0, b"x\n")])
    with setup.acquisition(runner) as repo:
        assert repo.read("ls-tree", ["-r", COMMIT], max_output_bytes=10).stdout == b"x\n"
        with pytest.raises(ValueError):
            repo.read("push", [], max_output_bytes=10)
        assert repo.deadline.remaining() > repo.deadline.working_time()
    assert runner.calls[-1].argv[-4:] == ["--git-dir=" + str(repo.git_dir), "ls-tree", "-r", COMMIT]
    assert runner.calls[-1].max_output_bytes == 10


def test_cli_never_sets_the_test_seam() -> None:
    package = ROOT / "src" / "devops_tooling_agent"
    for module in ("cli.py", "sweep.py", "resolve.py"):
        text = (package / module).read_text(encoding="utf-8")
        assert "source_url" not in text and "allow_file_protocol" not in text


# -- real Git with local bare repositories ----------------------------------------


@requires_git
def test_real_acquisition_success(setup: Setup, tmp_path: Path) -> None:
    source = make_source(tmp_path, commits=25, files={"README.md": b"fixture\n"})
    acquisition = setup.acquisition(run, source=source, git_path=setup.git_path())
    with acquisition as repo:
        git_dir = repo.git_dir
        assert repo.commit == head_commit(source)
        config = (git_dir / "config").read_text(encoding="utf-8").lower()
        for forbidden in (
            "remote",
            "credential",
            "helper",
            "url",
            "extraheader",
            "insteadof",
            "hookspath",
            "fsmonitor",
        ):
            assert forbidden not in config
        assert not (git_dir / "hooks").exists() or list((git_dir / "hooks").iterdir()) == []
        assert not (git_dir / "FETCH_HEAD").exists()
        assert (git_dir / "shallow").is_file()
        log = repo.read("log", ["--format=%H", repo.commit], max_output_bytes=65536)
        assert log.exit_code == 0
        assert len(log.stdout.split()) == FETCH_DEPTH
        assert not (git_dir / "modules").exists()
    assert acquisition.outcome == acquire.Cleanup(repo.workspace, True)
    assert setup.leftovers() == []


@requires_git
def test_real_fetch_failure_removes_the_workspace(setup: Setup, tmp_path: Path) -> None:
    missing = tmp_path / "missing.git"
    acquisition = setup.acquisition(run, source=missing, git_path=setup.git_path())
    with pytest.raises(CommandError) as raised:
        enter(acquisition)
    assert raised.value.code is ErrorCode.PROVIDER_ERROR
    assert raised.value.details["stage"] == "git_fetch"
    assert acquisition.outcome.removed is True
    assert setup.leftovers() == []


@requires_git
def test_real_exception_after_acquisition(setup: Setup, tmp_path: Path) -> None:
    source = make_source(tmp_path)
    acquisition = setup.acquisition(run, source=source, git_path=setup.git_path())
    with pytest.raises(RuntimeError), acquisition:
        raise RuntimeError("after the fetch")
    assert acquisition.outcome.removed is True
    assert setup.leftovers() == []


@requires_git
def test_real_interruption_inside_the_block(setup: Setup, tmp_path: Path) -> None:
    source = make_source(tmp_path)
    acquisition = setup.acquisition(run, source=source, git_path=setup.git_path())
    with pytest.raises(Interrupted), acquisition:
        raise Interrupted
    assert acquisition.outcome.removed is True
    assert setup.leftovers() == []


@requires_git
def test_real_object_check(setup: Setup, tmp_path: Path) -> None:
    source = make_malformed_source(tmp_path)
    acquisition = setup.acquisition(run, source=source, git_path=setup.git_path())
    expect_error(
        ErrorCode.UNSUPPORTED_REPOSITORY,
        {"reason": "object_check_failed"},
        lambda: enter(acquisition),
    )
    assert setup.leftovers() == []


@requires_git
def test_real_submodule_is_not_fetched(setup: Setup, tmp_path: Path) -> None:
    source = make_source(tmp_path, submodule=True)
    acquisition = setup.acquisition(run, source=source, git_path=setup.git_path())
    with acquisition as repo:
        tree = repo.read("ls-tree", [repo.commit], max_output_bytes=65536).stdout
        assert b"160000 commit" in tree
        assert not (repo.git_dir / "modules").exists()
    assert setup.leftovers() == []


@requires_git
def test_real_workspace_size_limit(
    setup: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(acquire, "MAX_WORKSPACE_BYTES", 256 * 1024)
    source = make_source(tmp_path, files={"blob.bin": os.urandom(2 * 1024 * 1024)})
    acquisition = setup.acquisition(run, source=source, git_path=setup.git_path())
    with pytest.raises(CommandError) as raised:
        enter(acquisition)
    assert raised.value.code is ErrorCode.LIMIT_EXCEEDED
    observed = raised.value.details["observed"]
    assert isinstance(observed, int) and observed > 256 * 1024
    assert setup.leftovers() == []


@requires_git
def test_real_inherited_configuration_never_runs(setup: Setup, tmp_path: Path) -> None:
    """Hooks, templates, fsmonitor, and helpers from inherited configuration never run."""
    source = make_source(tmp_path)
    config, hooks, marker = marker_configuration(tmp_path)
    home = tmp_path / "marker-home"
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".gitconfig").write_bytes(config.read_bytes())
    (home / ".config" / "git" / "config").write_bytes(config.read_bytes())
    environ = {
        **setup.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": hooks.as_posix(),
        "GIT_CONFIG_PARAMETERS": f"'core.hookspath'='{hooks.as_posix()}'",
        "GIT_TEMPLATE_DIR": str(tmp_path / "marker-template"),
    }
    acquisition = setup.acquisition(run, source=source, environ=environ, git_path=setup.git_path())
    with acquisition as repo:
        assert not (repo.git_dir / "template-artifact").exists()
    assert not marker.exists()
    assert setup.leftovers() == []


@requires_git
def test_real_marker_configuration_fires_without_isolation(tmp_path: Path) -> None:
    """Positive control: the same configuration runs its markers when not isolated."""
    from gitfixtures import GIT, fixture_env

    assert GIT is not None
    source = make_source(tmp_path)
    config, _, marker = marker_configuration(tmp_path)
    env = {**fixture_env(tmp_path), "GIT_CONFIG_GLOBAL": str(config)}
    target = tmp_path / "control.git"
    subprocess.run([GIT, "init", "-q", "--bare", str(target)], env=env, check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [
            GIT,
            f"--git-dir={target}",
            "fetch",
            "-q",
            source.as_uri(),
            "+refs/heads/main:refs/heads/main",
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    assert (target / "template-artifact").exists()
    assert marker.exists()


@requires_git
def test_real_isolation_check_detects_injected_configuration(setup: Setup, tmp_path: Path) -> None:
    """A variable that reaches Git despite the allowlist fails the self-check."""

    def leaky(argv: Sequence[str], *, env: Mapping[str, str], **kwargs: Any) -> RunResult:
        injected = {
            **env,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": "/injected",
        }
        return run(argv, env=injected, **kwargs)

    source = make_source(tmp_path)
    acquisition = setup.acquisition(leaky, source=source, git_path=setup.git_path())
    expect_error(
        ErrorCode.UNSAFE_ENVIRONMENT, {"check": "configuration_scope"}, lambda: enter(acquisition)
    )
    assert setup.leftovers() == []


@requires_git
def test_real_isolation_check_detects_url_rewrite(setup: Setup, tmp_path: Path) -> None:
    def rewriting(argv: Sequence[str], *, env: Mapping[str, str], **kwargs: Any) -> RunResult:
        if "ls-remote" in argv:
            injected = {
                **env,
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "url.https://evil.example/.insteadOf",
                "GIT_CONFIG_VALUE_0": "https://github.com/",
            }
            return run(argv, env=injected, **kwargs)
        return run(argv, env=env, **kwargs)

    acquisition = setup.acquisition(rewriting, git_path=setup.git_path())
    expect_error(ErrorCode.UNSAFE_ENVIRONMENT, {"check": "url_rewrite"}, lambda: enter(acquisition))
    assert setup.leftovers() == []


# -- real signals (POSIX) ---------------------------------------------------------

_SIGNAL_SCRIPT = """
import json, sys, time
from pathlib import Path
from devops_tooling_agent import acquire, git, interrupts
from devops_tooling_agent.environment import build_environment
from devops_tooling_agent.errors import CommandError, Interrupted
from devops_tooling_agent.identifier import Repository
from devops_tooling_agent.runner import run

temp_root, agent, source, ready = sys.argv[1:5]
environ = {"PATH": "/usr/bin:/bin", "TMPDIR": temp_root}
git_path = git.resolve_git(build_environment(environ, windows=False), cwd=agent, windows=False)

def runner(argv, **kwargs):
    if "fetch" in argv:
        # A long-running child in place of the fetch, so that the signal arrives
        # while a descendant runs.
        Path(ready).write_text(kwargs["cwd"].name)
        return run([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
    return run(argv, **kwargs)

interrupts.install_raising_handlers()
progress = interrupts.Progress("validation")
acquisition = acquire.Acquisition(
    repository=Repository("o", "r"), default_branch="main", disk_usage_kib=1,
    git_path=git_path, gh_path=Path("/usr/bin/gh"), environ=environ, windows=False,
    cwd=agent, runner=runner, deadline=acquire.Deadline.start(time.monotonic, 240),
    progress=progress, source_url=Path(source).as_uri(),
)
try:
    with acquisition:
        outcome = "acquired"
except Interrupted:
    outcome = "interrupted"
except CommandError as error:
    outcome = error.code.name
print(json.dumps({"outcome": outcome, "stage": progress.stage,
                  "removed": acquisition.outcome.removed}))
"""


@requires_git
@pytest.mark.skipif(WINDOWS, reason="POSIX signals")
@pytest.mark.parametrize("signum", ["SIGINT", "SIGTERM"])
def test_real_signal_during_fetch_cleans_up(setup: Setup, tmp_path: Path, signum: str) -> None:
    source = make_source(tmp_path)
    ready = tmp_path / "ready"
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-c",
            _SIGNAL_SCRIPT,
            str(setup.temp_root),
            str(setup.agent),
            str(source),
            str(ready),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 60
    while not ready.exists() or not ready.read_text():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the fetch stage was not reached"
        time.sleep(0.05)
    assert setup.leftovers() == [ready.read_text()]
    process.send_signal(getattr(signal, signum))
    stdout, stderr = process.communicate(timeout=60)
    assert process.returncode == 0, stderr
    assert json.loads(stdout) == {"outcome": "interrupted", "stage": "cleanup", "removed": True}
    assert setup.leftovers() == []

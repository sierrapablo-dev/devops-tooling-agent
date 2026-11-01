"""The production runner, exercised with the Python interpreter as the child.

No gh, git, or network access is involved.
"""

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from devops_tooling_agent.runner import DescendantsRemain, RunResult, run

PYTHON = sys.executable


def py(code: str, tmp_path: Path, *, timeout: float = 30, limit: int = 1024) -> RunResult:
    env = {key: value for key, value in os.environ.items() if key in {"SYSTEMROOT", "PATH"}}
    env["MARKER"] = "fixed-value"
    return run(
        [PYTHON, "-I", "-c", code],
        env=env,
        cwd=tmp_path,
        timeout=timeout,
        max_output_bytes=limit,
    )


def test_captures_exit_code_and_output(tmp_path: Path) -> None:
    result = py(
        "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)", tmp_path
    )
    assert result.exit_code == 3
    assert result.stdout == b"out"
    assert result.stderr == b"err"
    assert result.timed_out is False


def test_output_is_bounded(tmp_path: Path) -> None:
    result = py("import sys; sys.stdout.write('x' * 1000000)", tmp_path, limit=100)
    assert result.exit_code == 0
    assert result.stdout == b"x" * 100
    assert result.stdout_truncated is True
    assert result.stderr_truncated is False


def test_stdin_is_at_end_of_file(tmp_path: Path) -> None:
    result = py("import sys; print(repr(sys.stdin.read()))", tmp_path)
    assert result.stdout.strip() == b"''"


def test_environment_and_working_directory_are_explicit(tmp_path: Path) -> None:
    code = "import os; print(os.environ.get('MARKER'), os.environ.get('GH_TOKEN'), os.getcwd())"
    result = py(code, tmp_path)
    marker, inherited, cwd = result.stdout.decode().split()
    assert marker == "fixed-value"
    assert inherited == "None"
    assert os.path.samefile(cwd, tmp_path)


def test_timeout_kills_the_child(tmp_path: Path) -> None:
    started = time.monotonic()
    result = py("import time; time.sleep(60)", tmp_path, timeout=1)
    assert time.monotonic() - started < 30
    assert result.timed_out is True
    assert result.exit_code is None


def test_relative_executable_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        run(["python"], env={}, cwd=tmp_path, timeout=1, max_output_bytes=1)


# -- watch, termination, and containment -------------------------------------------

GRANDCHILD = """
import subprocess, sys, time
grandchild = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(grandchild.pid))
time.sleep(float(sys.argv[2]))
"""


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return code.value == 259
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in {"SYSTEMROOT", "PATH"}}


def spawn_grandchild(tmp_path: Path, child_seconds: float, timeout: float) -> tuple[RunResult, int]:
    pid_file = tmp_path / "grandchild.pid"
    result = run(
        [PYTHON, "-I", "-c", GRANDCHILD, str(pid_file), str(child_seconds)],
        env=environment(),
        cwd=tmp_path,
        timeout=timeout,
        max_output_bytes=1024,
    )
    return result, int(pid_file.read_text())


def test_timeout_terminates_every_descendant(tmp_path: Path) -> None:
    result, grandchild = spawn_grandchild(tmp_path, 60, 5)
    assert result.timed_out is True
    assert not alive(grandchild)


def test_normal_exit_terminates_remaining_descendants(tmp_path: Path) -> None:
    """A descendant that outlives the child, like detached maintenance, is terminated."""
    result, grandchild = spawn_grandchild(tmp_path, 0, 30)
    assert result.exit_code == 0 and result.timed_out is False
    assert not alive(grandchild)


def test_watch_aborts_the_run(tmp_path: Path) -> None:
    calls: list[float] = []

    def watch() -> bool:
        calls.append(time.monotonic())
        return len(calls) < 3

    started = time.monotonic()
    result = run(
        [PYTHON, "-I", "-c", "import time; time.sleep(60)"],
        env=environment(),
        cwd=tmp_path,
        timeout=30,
        max_output_bytes=1024,
        watch=watch,
        watch_interval=0.1,
    )
    assert result.aborted is True and result.timed_out is False and result.exit_code is None
    assert len(calls) == 3
    assert time.monotonic() - started < 20


def test_watch_is_not_called_after_a_quick_exit(tmp_path: Path) -> None:
    result = run(
        [PYTHON, "-I", "-c", "pass"],
        env=environment(),
        cwd=tmp_path,
        timeout=30,
        max_output_bytes=1024,
        watch=lambda: pytest.fail("watch called"),
        watch_interval=10,
    )
    assert result.exit_code == 0 and result.aborted is False


def test_unconfirmed_termination_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import devops_tooling_agent.runner as runner_module

    monkeypatch.setattr(runner_module, "_end_tree", lambda process, job: False)
    with pytest.raises(DescendantsRemain) as raised:
        run(
            [PYTHON, "-I", "-c", "pass"],
            env=environment(),
            cwd=tmp_path,
            timeout=30,
            max_output_bytes=1024,
        )
    assert raised.value.result is not None and raised.value.result.exit_code == 0


def test_unconfirmed_termination_after_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import devops_tooling_agent.runner as runner_module

    real_end_tree = runner_module._end_tree

    def unconfirmed(process: Any, job: Any) -> bool:
        real_end_tree(process, job)
        return False

    monkeypatch.setattr(runner_module, "_end_tree", unconfirmed)

    def failing_watch() -> bool:
        raise RuntimeError("watch failed")

    with pytest.raises(DescendantsRemain) as raised:
        run(
            [PYTHON, "-I", "-c", "import time; time.sleep(60)"],
            env=environment(),
            cwd=tmp_path,
            timeout=30,
            max_output_bytes=1024,
            watch=failing_watch,
            watch_interval=0.1,
        )
    assert raised.value.result is None
    assert isinstance(raised.value.__context__, RuntimeError)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")
def test_breakaway_does_not_escape_the_job(tmp_path: Path) -> None:
    """A grandchild created with CREATE_BREAKAWAY_FROM_JOB is still terminated.

    The job never sets JOB_OBJECT_LIMIT_BREAKAWAY_OK. Inside an outer job, as
    under Claude Code, the creation itself can succeed, but the grandchild stays
    in the runner's job (docs/claude-code-environment.md, Section 8.1.10).
    """
    pid_file = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'],"
        " creationflags=0x01000000)\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    try:
        result = run(
            [PYTHON, "-I", "-c", code, str(pid_file)],
            env=environment(),
            cwd=tmp_path,
            timeout=5,
            max_output_bytes=1024,
        )
    except DescendantsRemain:
        pytest.fail("termination was not confirmed")
    assert result.timed_out is True
    if pid_file.exists():  # the creation may also be refused, which is contained too
        assert not alive(int(pid_file.read_text()))

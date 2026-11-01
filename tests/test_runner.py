"""The production runner, exercised with the Python interpreter as the child.

No gh, git, or network access is involved.
"""

import os
import sys
import time
from pathlib import Path

import pytest

from devops_tooling_agent.runner import RunResult, run

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

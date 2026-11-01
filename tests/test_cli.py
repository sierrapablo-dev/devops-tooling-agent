"""Envelope and command-line contract (ADR-0007 Sections 3 to 8)."""

import io
import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import InvokeFn, resolve_steps, view
from devops_tooling_agent import __version__
from devops_tooling_agent.cli import main
from devops_tooling_agent.constants import MAX_LINE_CHARS
from devops_tooling_agent.envelope import failure, serialize, success
from devops_tooling_agent.errors import CommandError, ErrorCode

ROOT = Path(__file__).resolve().parents[1]
KEYS = [
    "schema",
    "schema_version",
    "tool",
    "command",
    "status",
    "result",
    "error",
    "warnings",
    "untrusted",
    "end",
]
SENTINEL = "NOT-A-REAL-CREDENTIAL-SENTINEL"


def check_document(raw: bytes) -> dict[str, Any]:
    """The invariants that every envelope satisfies."""
    raw.decode("ascii")  # ASCII only
    assert b"\r" not in raw
    assert raw.endswith(b"}\n")
    lines = raw.decode("ascii").split("\n")
    assert all(len(line) <= MAX_LINE_CHARS for line in lines)
    assert lines[1] == '  "schema": "devops-tooling-agent.envelope",'
    assert lines[-2] == "}" and lines[-3] == '  "end": true'
    envelope: dict[str, Any] = json.loads(raw)
    assert list(envelope) == KEYS
    assert envelope["schema_version"] == 1
    assert envelope["tool"] == {"name": "devops-tooling-agent", "version": __version__}
    assert envelope["end"] is True
    if envelope["status"] == "error":
        assert list(envelope["error"]) == ["code", "message", "details"]
    return envelope


USAGE_CASES = [
    [],
    ["repo"],
    ["repo", "resolve"],
    ["repo", "resolve", "a/b", "extra"],
    ["repo", "resolve", "--help"],
    ["-h"],
    ["--help"],
    ["--version"],
    ["repo", "-h"],
    ["repo", "res", "a/b"],
    ["repo", "resolve", "--ident", "a/b"],
    ["repo", "resolve", f"--{SENTINEL}=x"],
    ["repo", "resolve", "-x", SENTINEL],
    [SENTINEL],
    ["repo", SENTINEL],
    ["workspace", "sweep"],
    ["repo", "collect", "a/b"],
    ["repo", "resolve", "--", "a/b", SENTINEL],
    ["@" + SENTINEL],
]


@pytest.mark.parametrize("argv", USAGE_CASES)
def test_usage_errors(invoke: InvokeFn, capfd: pytest.CaptureFixture[str], argv: list[str]) -> None:
    run = invoke(argv)
    assert run.exit_code == 2
    envelope = check_document(run.raw)
    assert envelope["status"] == "error"
    assert envelope["error"] == {
        "code": "USAGE_ERROR",
        "message": ErrorCode.USAGE_ERROR.message,
        "details": {"expected": "repo resolve <identifier>"},
    }
    expected_command = "repo resolve" if argv[:2] == ["repo", "resolve"] else None
    assert envelope["command"] == expected_command
    assert SENTINEL.encode() not in run.raw
    for argument in argv:
        if argument not in {"repo", "resolve"}:
            assert f'"{argument}"'.encode() not in run.raw
    assert run.runner.calls == []
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""


def test_success_envelope(invoke: InvokeFn, capfd: pytest.CaptureFixture[str]) -> None:
    run = invoke(["repo", "resolve", "octocat/Hello-World"], resolve_steps(view()))
    envelope = check_document(run.raw)
    assert run.exit_code == 0
    assert envelope["status"] == "ok"
    assert envelope["command"] == "repo resolve"
    assert envelope["error"] is None
    assert envelope["warnings"] == [] and envelope["untrusted"] == []
    assert capfd.readouterr().err == ""


@pytest.mark.parametrize(
    "identifier",
    [
        f"https://{SENTINEL}@github.com/o/r",
        f"https://user:{SENTINEL}@github.com/o/r",
        f"{SENTINEL}@github.com:o/r",
        f"https://github.com/o/r?token={SENTINEL}",
        f"https://github.com/o/r#{SENTINEL}",
        f"o/r/{SENTINEL}",
        f"{SENTINEL}é/r",
        f"{SENTINEL}" * 20,
    ],
)
def test_rejected_identifiers_are_never_echoed(
    invoke: InvokeFn, capfd: pytest.CaptureFixture[str], identifier: str
) -> None:
    run = invoke(["repo", "resolve", identifier])
    envelope = check_document(run.raw)
    assert run.exit_code == 1
    assert envelope["error"]["code"] == "INVALID_IDENTIFIER"
    assert SENTINEL.encode() not in run.raw
    assert capfd.readouterr().err == ""


def test_error_exit_codes() -> None:
    for code in ErrorCode:
        expected = {"USAGE_ERROR": 2, "INTERRUPTED": 130}.get(code.name, 1)
        assert code.exit_code == expected


def test_details_must_match_documented_keys() -> None:
    with pytest.raises(ValueError):
        CommandError(ErrorCode.TIMEOUT, {"stage": "x", "extra": 1})
    with pytest.raises(ValueError):
        CommandError(ErrorCode.TIMEOUT)


def test_non_ascii_values_are_escaped() -> None:
    raw = serialize(success("repo resolve", {"value": "café ‮"}), "repo resolve")
    check_document(raw)
    assert b"\\u00e9" in raw and b"\\u202e" in raw


def test_overlong_line_becomes_internal_error() -> None:
    raw = serialize(success("repo resolve", {"value": "x" * MAX_LINE_CHARS}), "repo resolve")
    envelope = check_document(raw)
    assert envelope["error"]["code"] == "INTERNAL_ERROR"
    assert envelope["command"] == "repo resolve"


def test_unserializable_value_becomes_internal_error() -> None:
    raw = serialize(success("repo resolve", {"value": float("nan")}), None)
    assert check_document(raw)["error"]["code"] == "INTERNAL_ERROR"


def test_failure_envelope_shape() -> None:
    error = CommandError(ErrorCode.PROVIDER_ERROR, {"exit_code": 1, "stage": "gh_repo_view"})
    envelope = failure("repo resolve", error)
    assert list(envelope["error"]["details"]) == ["stage", "exit_code"]


class _BrokenStream(io.RawIOBase):
    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        raise OSError("broken pipe")


def test_stdout_write_failure(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = main(
        ["repo", "resolve", "bad identifier"],
        runner=lambda *a, **k: pytest.fail("no subprocess expected"),
        environ={},
        windows=False,
        cwd=str(tmp_path),
        stdout=_BrokenStream(),  # type: ignore[arg-type]
        stderr=stderr,
    )
    assert code == 1
    assert stderr.getvalue() == (
        "devops-tooling-agent: the envelope could not be written to standard output\n"
    )


def test_signal_handlers_are_restored(invoke: InvokeFn) -> None:
    before = signal.getsignal(signal.SIGINT)
    invoke(["repo", "resolve", "bad identifier"])
    assert signal.getsignal(signal.SIGINT) is before


def test_warnings_do_not_reach_stderr(invoke: InvokeFn, capfd: pytest.CaptureFixture[str]) -> None:
    def noisy(*args: Any, **kwargs: Any) -> Any:
        import warnings

        warnings.warn("NOISY-WARNING", RuntimeWarning, stacklevel=1)
        sys.stderr.write("NOISY-STDERR\n")
        raise RuntimeError("boom")

    run = invoke(["repo", "resolve", "a/b"], [noisy])
    assert check_document(run.raw)["error"]["code"] == "INTERNAL_ERROR"
    captured = capfd.readouterr()
    assert captured.err == ""


@pytest.mark.parametrize(
    ("argv", "exit_code", "code"),
    [
        (["repo", "resolve"], 2, "USAGE_ERROR"),
        (["repo", "resolve", f"https://{SENTINEL}@github.com/o/r"], 1, "INVALID_IDENTIFIER"),
    ],
)
def test_module_invocation_bytes(argv: list[str], exit_code: int, code: str) -> None:
    """Real ``python -I -m`` process: raw bytes, LF only, and empty stderr.

    Only the interpreter is started. Both cases fail before any gh call.
    """
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-m", "devops_tooling_agent", *argv],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == exit_code
    assert completed.stderr == b""
    envelope = check_document(completed.stdout)
    assert envelope["error"]["code"] == code
    assert SENTINEL.encode() not in completed.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and session kill")
@pytest.mark.parametrize("signum", ["SIGINT", "SIGTERM"])
def test_real_interruption_kills_the_child(tmp_path: Path, signum: str) -> None:
    """A real signal during a gh call yields INTERRUPTED and no surviving child.

    The fake gh is a shell script that records its process ID and sleeps.
    """
    import os
    import time

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pid_file = tmp_path / "gh.pid"
    fake = bin_dir / "gh"
    fake.write_text(f'#!/bin/sh\necho $$ > "{pid_file}"\nexec sleep 60\n', encoding="utf-8")
    fake.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-I", "-m", "devops_tooling_agent", "repo", "resolve", "o/r"],
        cwd=ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 30
    while not pid_file.exists() or not pid_file.read_text().strip():
        assert time.monotonic() < deadline, "the fake gh did not start"
        time.sleep(0.05)
    child = int(pid_file.read_text())
    process.send_signal(getattr(signal, signum))
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 130
    assert stderr == b""
    envelope = check_document(stdout)
    assert envelope["error"]["code"] == "INTERRUPTED"
    assert envelope["error"]["details"] == {"stage": "gh_version"}
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)

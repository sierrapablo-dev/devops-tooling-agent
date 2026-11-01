"""The only module that starts processes (ADR-0007 Section 9).

Every subprocess runs with an explicit argument vector, environment, working
directory, timeout, and output bound. Standard input is the null device, so a
child that reads it gets end-of-file immediately. No shell is used, and output
is captured, never forwarded.

Process termination after a timeout or an interruption:

- POSIX: the child starts in a new session, and the whole session is killed.
- Windows (interim, until the Job Object of #17): only the direct child is
  terminated. That is sufficient only when the started executable is the real
  program and not a launcher. MSI and winget installations of ``gh`` install the
  real binary. Scoop and Chocolatey install ``gh.exe`` as a shim that starts the
  real binary as a child, and terminating the shim can leave that child running.
  This runner is therefore used only for ``gh``, never for Git, whose Windows
  executable is a launcher (docs/claude-code-environment.md, Section 8.1.2).
"""

import os
import signal

# This module is the single, reviewed subprocess seam (ADR-0007 Section 9).
import subprocess  # nosec B404
import sys
import threading
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

# Bound on waiting for the output readers after the child has ended.
_READER_JOIN_SECONDS = 2.0
_READ_CHUNK_BYTES = 65536


@dataclass(frozen=True)
class RunResult:
    """Outcome of one subprocess.

    ``exit_code`` is ``None`` when the timeout expired and the process was killed.
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


class Runner(Protocol):
    """The subprocess runner interface. Tests inject their own implementation."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float,
        max_output_bytes: int,
    ) -> RunResult: ...


class _Capture:
    """Reads a pipe to its end, keeping at most ``limit`` bytes."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._data = bytearray()
        self.truncated = False
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self) -> None:
        read1 = getattr(self._stream, "read1", self._stream.read)
        try:
            while True:
                chunk = read1(_READ_CHUNK_BYTES)
                if not chunk:
                    return
                room = self._limit - len(self._data)
                if room > 0:
                    self._data += chunk[:room]
                if len(chunk) > room:
                    # Keep draining so that the child never blocks on a full pipe.
                    self.truncated = True
        except (OSError, ValueError):
            self.truncated = True

    def result(self) -> tuple[bytes, bool]:
        if self.thread.is_alive():
            # A descendant still holds the pipe. The captured part is incomplete.
            return bytes(self._data), True
        return bytes(self._data), self.truncated


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        # Interim mechanism, see the module docstring.
        process.kill()
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
) -> RunResult:
    """Run one subprocess with captured, bounded output and a timeout."""
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("the executable must be an absolute path")
    # Fixed argument vector with an absolute executable, and no shell.
    process = subprocess.Popen(  # noqa: S603  # nosec B603
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        cwd=cwd,
        shell=False,
        close_fds=True,
        start_new_session=sys.platform != "win32",
    )
    if process.stdout is None or process.stderr is None:
        _terminate(process)
        raise RuntimeError("output pipes are missing")
    out = _Capture(process.stdout, max_output_bytes)
    err = _Capture(process.stderr, max_output_bytes)
    out.thread.start()
    err.thread.start()

    timed_out = False
    try:
        try:
            exit_code: int | None = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = None
            _terminate(process)
    except BaseException:
        # Interrupted or unexpected: never leave the child running.
        if process.poll() is None:
            _terminate(process)
        raise
    finally:
        out.thread.join(_READER_JOIN_SECONDS)
        err.thread.join(_READER_JOIN_SECONDS)

    stdout, stdout_truncated = out.result()
    stderr, stderr_truncated = err.result()
    for capture, stream in ((out, process.stdout), (err, process.stderr)):
        if not capture.thread.is_alive():
            stream.close()
    return RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )

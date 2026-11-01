"""The only module that starts processes (ADR-0007 Section 9).

Every subprocess runs with an explicit argument vector, environment, working
directory, timeout, and output bound. Standard input is the null device, so a
child that reads it gets end-of-file immediately. No shell is used, and output
is captured, never forwarded.

Every run ends by terminating the whole process tree of the child and
confirming that no descendant remains, whether the child exited, timed out, was
aborted by the watch callback, or the caller was interrupted
(ADR-0006 Section 7, security policy Section 5.7.3, rule 6):

- POSIX: the child starts in a new session. The session's process group is
  killed, and the run waits until the group is empty.
- Windows: the child is created suspended, assigned to a new Job Object before
  it runs any code, and only then resumed. Every descendant is therefore in the
  job from its creation. The job is terminated, and the run waits until it has
  no active process. The job never allows breakaway, and it kills its
  processes when its last handle is closed, including when this process dies.
  This was chosen with the criterion of ADR-0006 Section 7: it passed the
  orphan reproduction of docs/claude-code-environment.md, Section 8.1.2
  (Section 8.1.10).

If no-descendant cannot be confirmed within ``TERMINATION_WAIT_SECONDS``, the
run raises ``DescendantsRemain``. The caller must then not remove any data that
a descendant could still be using.
"""

import os
import signal

# This module is the single, reviewed subprocess seam (ADR-0007 Section 9).
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from devops_tooling_agent.constants import TERMINATION_WAIT_SECONDS

# Bound on waiting for the output readers after the tree has ended.
_READER_JOIN_SECONDS = 2.0
_READ_CHUNK_BYTES = 65536
# Interval of the no-descendant check after termination.
_TERMINATION_POLL_SECONDS = 0.02


@dataclass(frozen=True)
class RunResult:
    """Outcome of one subprocess.

    ``exit_code`` is ``None`` when the timeout expired or the watch callback
    aborted the run, and the process tree was killed.
    """

    exit_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    aborted: bool = False


class DescendantsRemain(Exception):
    """No-descendant could not be confirmed after the run.

    ``result`` is the outcome of the run when it completed, and ``None`` when
    the run was ended by an exception, which is then the ``__context__``.
    """

    def __init__(self, result: RunResult | None) -> None:
        super().__init__("descendant processes could not be confirmed terminated")
        self.result = result


Watch = Callable[[], bool]


class Runner(Protocol):
    """The subprocess runner interface. Tests inject their own implementation.

    ``watch``, when given, is called about every ``watch_interval`` seconds
    while the child runs. When it returns ``False``, the process tree is
    terminated and the result has ``aborted`` set.
    """

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float,
        max_output_bytes: int,
        watch: Watch | None = None,
        watch_interval: float = 1.0,
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


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _CREATION_FLAGS = _CREATE_SUSPENDED
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_BASIC_ACCOUNTING_INFORMATION = 1
    _JOB_EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100
    _THREAD_SUSPEND_RESUME = 0x0002
    _TH32CS_SNAPTHREAD = 0x00000004
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _RESUME_FAILED = 0xFFFFFFFF

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    _kernel32.Thread32Next.restype = wintypes.BOOL

    def _win_error(function: str) -> OSError:
        return OSError(ctypes.get_last_error(), f"{function} failed")

    def _resume_threads(pid: int) -> None:
        """Resume the threads of a process created suspended."""
        snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
        if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
            raise _win_error("CreateToolhelp32Snapshot")
        resumed = 0
        try:
            entry = _ThreadEntry32()
            entry.dwSize = ctypes.sizeof(_ThreadEntry32)
            found = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if not thread:
                        raise _win_error("OpenThread")
                    try:
                        if _kernel32.ResumeThread(thread) == _RESUME_FAILED:
                            raise _win_error("ResumeThread")
                    finally:
                        _kernel32.CloseHandle(thread)
                    resumed += 1
                entry.dwSize = ctypes.sizeof(_ThreadEntry32)
                found = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snapshot)
        if resumed == 0:
            raise OSError(0, "no thread of the suspended child was found")

    class _Job:
        """A Job Object that kills its processes when closed and allows no breakaway."""

        def __init__(self) -> None:
            handle = _kernel32.CreateJobObjectW(None, None)
            if not handle:
                raise _win_error("CreateJobObjectW")
            self._handle = handle
            info = _ExtendedLimitInformation()
            # Only KILL_ON_JOB_CLOSE. JOB_OBJECT_LIMIT_BREAKAWAY_OK and
            # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK are never set.
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                handle,
                _JOB_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                error = _win_error("SetInformationJobObject")
                self.close()
                raise error

        def assign_and_resume(self, pid: int) -> None:
            # The handle comes from the pid of the suspended child, not from Popen.
            process = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            if not process:
                raise _win_error("OpenProcess")
            try:
                if not _kernel32.AssignProcessToJobObject(self._handle, process):
                    raise _win_error("AssignProcessToJobObject")
            finally:
                _kernel32.CloseHandle(process)
            _resume_threads(pid)

        def active_processes(self) -> int:
            info = _BasicAccountingInformation()
            if not _kernel32.QueryInformationJobObject(
                self._handle,
                _JOB_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
                None,
            ):
                raise _win_error("QueryInformationJobObject")
            return int(info.ActiveProcesses)

        def terminate(self) -> None:
            # Failure is not fatal here: the confirmation below decides.
            _kernel32.TerminateJobObject(self._handle, 1)

        def close(self) -> None:
            if self._handle:
                _kernel32.CloseHandle(self._handle)
                self._handle = None

    def _end_tree(process: "subprocess.Popen[bytes]", job: _Job) -> bool:
        """Terminate the job and confirm that it has no active process."""
        deadline = time.monotonic() + TERMINATION_WAIT_SECONDS
        job.terminate()
        with suppress(OSError, subprocess.TimeoutExpired):
            process.wait(timeout=TERMINATION_WAIT_SECONDS)
        while True:
            if job.active_processes() == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_TERMINATION_POLL_SECONDS)

else:
    _CREATION_FLAGS = 0

    def _end_tree(process: "subprocess.Popen[bytes]", job: None) -> bool:
        """Kill the child's session and confirm that its process group is empty."""
        deadline = time.monotonic() + TERMINATION_WAIT_SECONDS
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        while True:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(_TERMINATION_POLL_SECONDS)


def _wait(
    process: "subprocess.Popen[bytes]",
    timeout: float,
    watch: Watch | None,
    watch_interval: float,
) -> tuple[int | None, bool, bool]:
    """Wait for the child. Returns the exit code, ``timed_out``, and ``aborted``."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, True, False
        step = remaining if watch is None else min(remaining, watch_interval)
        try:
            return process.wait(timeout=step), False, False
        except subprocess.TimeoutExpired:
            pass
        if watch is not None and not watch():
            return None, False, True


def _start(argv: Sequence[str], env: Mapping[str, str], cwd: Path) -> "subprocess.Popen[bytes]":
    # Fixed argument vector with an absolute executable, and no shell.
    return subprocess.Popen(  # noqa: S603  # nosec B603
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        cwd=cwd,
        shell=False,
        close_fds=True,
        start_new_session=sys.platform != "win32",
        creationflags=_CREATION_FLAGS,
    )


def run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float,
    max_output_bytes: int,
    watch: Watch | None = None,
    watch_interval: float = 1.0,
) -> RunResult:
    """Run one subprocess tree with captured, bounded output and a timeout.

    Raises ``DescendantsRemain`` when no-descendant cannot be confirmed.
    """
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("the executable must be an absolute path")

    if sys.platform == "win32":
        job = _Job()
        try:
            process = _start(argv, env, cwd)
        except BaseException:
            job.close()
            raise
        try:
            job.assign_and_resume(process.pid)
        except BaseException:
            # The child is still suspended and has run no code.
            process.kill()
            process.wait()
            job.close()
            raise
    else:
        job = None
        process = _start(argv, env, cwd)

    if process.stdout is None or process.stderr is None:
        contained = _end_tree(process, job)
        if job is not None:
            job.close()
        if not contained:
            raise DescendantsRemain(None)
        raise RuntimeError("output pipes are missing")
    out = _Capture(process.stdout, max_output_bytes)
    err = _Capture(process.stderr, max_output_bytes)
    out.thread.start()
    err.thread.start()

    exit_code: int | None = None
    timed_out = aborted = False
    contained = False
    try:
        try:
            exit_code, timed_out, aborted = _wait(process, timeout, watch, watch_interval)
        finally:
            # Interrupted or not, never leave a descendant running.
            try:
                contained = _end_tree(process, job)
            except OSError:
                contained = False
    except BaseException:
        if not contained:
            raise DescendantsRemain(None)  # noqa: B904  # the cause is __context__
        raise
    finally:
        out.thread.join(_READER_JOIN_SECONDS)
        err.thread.join(_READER_JOIN_SECONDS)
        if job is not None:
            job.close()

    stdout, stdout_truncated = out.result()
    stderr, stderr_truncated = err.result()
    for capture, stream in ((out, process.stdout), (err, process.stderr)):
        if not capture.thread.is_alive():
            stream.close()
    result = RunResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        aborted=aborted,
    )
    if not contained:
        raise DescendantsRemain(result)
    return result

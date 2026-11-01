"""Command-line interface (ADR-0007).

Every invocation writes exactly one envelope to standard output and nothing to
standard error. The only exception is a single fixed line on standard error
when the envelope cannot be written.
"""

import argparse
import io
import os
import signal
import sys
import threading
import time
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from types import FrameType
from typing import IO, Any, Final, NoReturn

from devops_tooling_agent.envelope import failure, serialize, success
from devops_tooling_agent.errors import EXIT_OK, CommandError, ErrorCode, Interrupted
from devops_tooling_agent.resolve import COMMAND as RESOLVE_COMMAND
from devops_tooling_agent.resolve import Progress, resolve
from devops_tooling_agent.runner import Runner
from devops_tooling_agent.runner import run as run_subprocess

PROG: Final = "devops_tooling_agent"
# Fixed syntax text. It never contains a received argument.
EXPECTED_SYNTAX: Final = "repo resolve <identifier>"
WRITE_FAILURE_LINE: Final = (
    "devops-tooling-agent: the envelope could not be written to standard output\n"
)


class _UsageError(Exception):
    """A parser error. Carries no message, because messages echo arguments."""


class _Parser(argparse.ArgumentParser):
    """An ``argparse`` parser that never prints and never exits."""

    def error(self, message: str) -> NoReturn:
        raise _UsageError

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise _UsageError

    # Help options are disabled and error() and exit() are overridden, so no
    # argparse path reaches print_usage(), print_help(), or _print_message().


def _new_parser(prog: str) -> _Parser:
    return _Parser(
        prog=prog,
        add_help=False,
        allow_abbrev=False,
        fromfile_prefix_chars=None,
    )


def build_parser() -> _Parser:
    parser = _new_parser(PROG)
    groups = parser.add_subparsers(dest="group", required=True, parser_class=_Parser)
    repo = groups.add_parser("repo", add_help=False, allow_abbrev=False)
    commands = repo.add_subparsers(dest="command", required=True, parser_class=_Parser)
    resolve_parser = commands.add_parser("resolve", add_help=False, allow_abbrev=False)
    resolve_parser.add_argument("identifier")
    return parser


def _parse(argv: Sequence[str]) -> argparse.Namespace | None:
    try:
        return build_parser().parse_args(list(argv))
    except (_UsageError, argparse.ArgumentError):
        return None


class _NullWriter(io.TextIOBase):
    """Replaces ``sys.stderr`` during an invocation, so nothing reaches it."""

    def write(self, text: str) -> int:
        return len(text)


def _raise_interrupted(signum: int, frame: FrameType | None) -> None:
    raise Interrupted


def _signals() -> list[signal.Signals]:
    names = ("SIGINT", "SIGTERM")
    return [getattr(signal, name) for name in names if hasattr(signal, name)]


@contextmanager
def _quiet_invocation() -> Iterator[None]:
    """Suppress warnings, stderr, and thread exception reports; trap signals."""
    saved_stderr = sys.stderr
    saved_excepthook = threading.excepthook
    saved_handlers: dict[signal.Signals, Any] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sys.stderr = _NullWriter()
        threading.excepthook = lambda args: None
        try:
            for signum in _signals():
                # ValueError outside the main thread; OSError if unsupported.
                with suppress(ValueError, OSError):
                    saved_handlers[signum] = signal.signal(signum, _raise_interrupted)
            yield
        finally:
            for signum in saved_handlers:
                # Ignore late signals while the envelope is written.
                with suppress(ValueError, OSError):
                    signal.signal(signum, signal.SIG_IGN)
            sys.stderr = saved_stderr
            threading.excepthook = saved_excepthook


def _restore_signals(handlers: Mapping[signal.Signals, Any]) -> None:
    for signum, handler in handlers.items():
        with suppress(ValueError, OSError, TypeError):
            signal.signal(signum, handler)


def _run_command(
    argv: Sequence[str],
    *,
    runner: Runner,
    environ: Mapping[str, str],
    windows: bool,
    cwd: str | None,
    clock: Callable[[], float],
) -> tuple[dict[str, Any], int]:
    command: str | None = None
    progress = Progress()
    try:
        working_directory = os.getcwd() if cwd is None else cwd
        namespace = _parse(argv)
        if namespace is None:
            if list(argv[:2]) == RESOLVE_COMMAND.split():
                command = RESOLVE_COMMAND
            usage = CommandError(ErrorCode.USAGE_ERROR, {"expected": EXPECTED_SYNTAX})
            return failure(command, usage), usage.code.exit_code
        command = RESOLVE_COMMAND
        result = resolve(
            namespace.identifier,
            runner=runner,
            environ=environ,
            windows=windows,
            cwd=working_directory,
            clock=clock,
            progress=progress,
        )
        return success(command, result), EXIT_OK
    except CommandError as error:
        return failure(command, error), error.code.exit_code
    except (Interrupted, KeyboardInterrupt):
        interrupted = CommandError(ErrorCode.INTERRUPTED, {"stage": progress.stage})
        return failure(command, interrupted), interrupted.code.exit_code
    except Exception:
        # No traceback and no exception text: either could contain an input.
        internal = CommandError(ErrorCode.INTERNAL_ERROR)
        return failure(command, internal), internal.code.exit_code


def _write(data: bytes, stdout: IO[bytes] | None, stderr: IO[str] | None) -> bool:
    try:
        target = stdout if stdout is not None else sys.stdout.buffer
        target.write(data)
        target.flush()
        return True
    except (OSError, ValueError, AttributeError):
        pass
    if stdout is None:
        try:
            # Keep the interpreter from reporting the broken stream at shutdown.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            os.close(devnull)
        except (OSError, ValueError, AttributeError):
            pass
    try:
        error_stream = stderr if stderr is not None else sys.__stderr__
        if error_stream is not None:
            error_stream.write(WRITE_FAILURE_LINE)
            error_stream.flush()
    except (OSError, ValueError, AttributeError):
        pass
    return False


def main(
    argv: Sequence[str],
    *,
    runner: Runner = run_subprocess,
    environ: Mapping[str, str] | None = None,
    windows: bool | None = None,
    cwd: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    stdout: IO[bytes] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    """Run one invocation and return its exit code."""
    saved_handlers = {signum: signal.getsignal(signum) for signum in _signals()}
    try:
        try:
            with _quiet_invocation():
                envelope, exit_code = _run_command(
                    argv,
                    runner=runner,
                    environ=os.environ if environ is None else environ,
                    windows=sys.platform == "win32" if windows is None else windows,
                    cwd=cwd,
                    clock=clock,
                )
                command = envelope.get("command")
                data = serialize(envelope, command if isinstance(command, str) else None)
        except (Interrupted, KeyboardInterrupt):
            # A signal between the command and the end of the quiet section.
            late = CommandError(ErrorCode.INTERRUPTED, {"stage": "output"})
            data = serialize(failure(None, late), None)
            exit_code = late.code.exit_code
        if not _write(data, stdout, stderr):
            return exit_code if exit_code != EXIT_OK else 1
        return exit_code
    finally:
        _restore_signals(saved_handlers)

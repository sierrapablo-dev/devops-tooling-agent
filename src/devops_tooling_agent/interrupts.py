"""Signal handling shared by every command (ADR-0006 Section 3, ADR-0007 Section 8).

``SIGINT`` and ``SIGTERM`` raise ``Interrupted`` while a command runs. Cleanup
that must finish, such as terminating descendants and removing a workspace,
runs under ``deferred``, which records signals instead of raising them.
"""

import signal
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from types import FrameType
from typing import Any

from devops_tooling_agent.errors import Interrupted


@dataclass
class Progress:
    """The current stage, read when the invocation is interrupted."""

    stage: str


def signals() -> list[signal.Signals]:
    names = ("SIGINT", "SIGTERM")
    return [getattr(signal, name) for name in names if hasattr(signal, name)]


def raise_interrupted(signum: int, frame: FrameType | None) -> None:
    raise Interrupted


def install_raising_handlers() -> dict[signal.Signals, Any]:
    """Install handlers that raise ``Interrupted``. Returns the replaced handlers."""
    saved: dict[signal.Signals, Any] = {}
    for signum in signals():
        # ValueError outside the main thread; OSError if unsupported.
        with suppress(ValueError, OSError):
            saved[signum] = signal.signal(signum, raise_interrupted)
    return saved


@contextmanager
def deferred() -> Iterator[list[int]]:
    """Record ``SIGINT`` and ``SIGTERM`` instead of raising them.

    Yields the list of signals received. The previous handlers are restored on
    exit. The caller decides whether to raise ``Interrupted`` afterwards.
    """
    received: list[int] = []

    def record(signum: int, frame: FrameType | None) -> None:
        received.append(signum)

    saved: dict[signal.Signals, Any] = {}
    try:
        for signum in signals():
            with suppress(ValueError, OSError):
                saved[signum] = signal.signal(signum, record)
        yield received
    finally:
        for signum, handler in saved.items():
            # A handler installed outside Python is reported as None.
            with suppress(ValueError, OSError, TypeError):
                signal.signal(signum, handler)

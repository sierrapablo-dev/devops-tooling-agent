"""The shared signal guard (ADR-0006 Section 3)."""

import signal

import pytest

from devops_tooling_agent import interrupts
from devops_tooling_agent.errors import Interrupted


def test_raising_handlers() -> None:
    previous = signal.getsignal(signal.SIGINT)
    try:
        saved = interrupts.install_raising_handlers()
        assert signal.SIGINT in saved
        with pytest.raises(Interrupted):
            signal.raise_signal(signal.SIGINT)
    finally:
        signal.signal(signal.SIGINT, previous)


def test_deferred_records_and_restores() -> None:
    previous = signal.getsignal(signal.SIGINT)
    try:
        interrupts.install_raising_handlers()
        with interrupts.deferred() as received:
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
        assert received == [signal.SIGINT, signal.SIGINT]
        assert signal.getsignal(signal.SIGINT) is interrupts.raise_interrupted
    finally:
        signal.signal(signal.SIGINT, previous)


def test_deferred_without_signals() -> None:
    with interrupts.deferred() as received:
        pass
    assert received == []

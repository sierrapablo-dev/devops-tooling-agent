"""``workspace sweep`` through the command-line interface (ADR-0006 Section 10)."""

import os
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import InvokeFn
from devops_tooling_agent import workspace
from devops_tooling_agent.constants import (
    MAX_SWEEP_ENTRIES,
    SWEEP_MIN_AGE_SECONDS,
    WORKSPACE_PREFIX,
)
from devops_tooling_agent.envelope import serialize, success
from devops_tooling_agent.errors import Interrupted
from test_cli import SENTINEL, check_document
from test_workspace import make, name, symlink_or_skip


@pytest.fixture
def temp_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Outside tmp_path, which is the agent environment of the invocation.
    return tmp_path_factory.mktemp("system-temp")


@pytest.fixture
def env(environ: dict[str, str], temp_root: Path) -> dict[str, str]:
    return {**environ, "TMPDIR": str(temp_root)}


def old() -> float:
    return time.time() - SWEEP_MIN_AGE_SECONDS - 60


def sweep(invoke: InvokeFn, env: dict[str, str], *args: str) -> tuple[int, dict[str, Any]]:
    run = invoke(["workspace", "sweep", *args], env=env)
    assert run.runner.calls == []
    return run.exit_code, check_document(run.raw)


def test_listing(invoke: InvokeFn, env: dict[str, str], temp_root: Path) -> None:
    make(temp_root, name("a"), created=old())
    make(temp_root, name("b"), created=time.time())
    code, envelope = sweep(invoke, env)
    assert code == 0
    assert envelope["command"] == "workspace sweep"
    assert envelope["warnings"] == [] and envelope["untrusted"] == []
    result = envelope["result"]
    assert list(result) == ["workspaces", "total", "truncated"]
    assert result["total"] == 2 and result["truncated"] is False
    first, second = result["workspaces"]
    assert list(first) == ["name", "age_seconds", "eligible", "reason"]
    assert first["name"] == name("a") and first["eligible"] is True and first["reason"] is None
    assert second["name"] == name("b") and second["eligible"] is False
    assert second["reason"] == "age"
    assert (temp_root / name("a")).exists()


def test_empty_listing(invoke: InvokeFn, env: dict[str, str]) -> None:
    code, envelope = sweep(invoke, env)
    assert code == 0
    assert envelope["result"] == {"workspaces": [], "total": 0, "truncated": False}


def test_remove_eligible(invoke: InvokeFn, env: dict[str, str], temp_root: Path) -> None:
    make(temp_root, name("a"), created=old())
    make(temp_root, name("b"), created=old())
    code, envelope = sweep(invoke, env, "--remove", name("a"))
    assert code == 0
    assert envelope["result"] == {"removed": name("a")}
    assert not (temp_root / name("a")).exists()
    assert (temp_root / name("b")).exists()


def ineligible(temp_root: Path, reason: str) -> str:
    if reason == "not_found":
        return name("c")
    if reason == "link":
        target = make(temp_root, "link-target", created=old())
        symlink_or_skip(temp_root / name("d"), target, True)
        return name("d")
    if reason == "marker":
        make(temp_root, name("e"), marker=b"{}")
        return name("e")
    make(temp_root, name("f"), created=time.time())
    return name("f")


@pytest.mark.parametrize("reason", ["not_found", "link", "marker", "age"])
def test_ineligible_workspaces_are_never_removed(
    invoke: InvokeFn, env: dict[str, str], temp_root: Path, reason: str
) -> None:
    target = ineligible(temp_root, reason)
    before = sorted(path.name for path in temp_root.iterdir())
    code, envelope = sweep(invoke, env, "--remove", target)
    assert code == 1
    assert envelope["error"]["code"] == "WORKSPACE_NOT_ELIGIBLE"
    assert envelope["error"]["details"] == {"reason": reason}
    assert sorted(path.name for path in temp_root.iterdir()) == before
    if reason == "link":
        assert (temp_root / "link-target" / ".devops-tooling-agent-workspace.json").exists()


def test_location(
    invoke: InvokeFn, env: dict[str, str], temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(temp_root, name(), created=old())
    real = os.path.realpath

    def moved(path: Any) -> str:
        if str(path).endswith(name()):
            return str(temp_root / "elsewhere" / name())
        return real(path)

    monkeypatch.setattr(os.path, "realpath", moved)
    code, envelope = sweep(invoke, env, "--remove", name())
    assert envelope["error"]["details"] == {"reason": "location"}
    assert (temp_root / name()).exists()


@pytest.mark.parametrize(
    "value",
    [
        SENTINEL,
        f"../{SENTINEL}",
        f"{WORKSPACE_PREFIX}{SENTINEL}",
        f"{WORKSPACE_PREFIX}{'0' * 31}/",
        f"C:\\{SENTINEL}",
        f"{SENTINEL}\u00e9",
        "",
    ],
)
def test_invalid_names_are_never_echoed(
    invoke: InvokeFn,
    env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("filesystem access before validation")

    monkeypatch.setattr(workspace, "temporary_root", forbidden)
    run = invoke(["workspace", "sweep", f"--remove={value}"], env=env)
    envelope = check_document(run.raw)
    assert envelope["error"]["code"] == "WORKSPACE_NOT_ELIGIBLE"
    assert envelope["error"]["details"] == {"reason": "name"}
    assert SENTINEL.encode() not in run.raw


def test_cleanup_failure(
    invoke: InvokeFn, env: dict[str, str], temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(temp_root, name(), created=old())
    monkeypatch.setattr(workspace, "remove", lambda *args, **kwargs: False)
    code, envelope = sweep(invoke, env, "--remove", name())
    assert code == 1
    assert envelope["error"]["code"] == "CLEANUP_FAILED"
    assert envelope["error"]["details"] == {"workspace": name(), "prior_error": None}


def test_interruption_during_removal(
    invoke: InvokeFn, env: dict[str, str], temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make(temp_root, name(), created=old())

    def interrupted(*args: object, **kwargs: object) -> bool:
        raise Interrupted

    monkeypatch.setattr(workspace, "remove", interrupted)
    code, envelope = sweep(invoke, env, "--remove", name())
    assert code == 130
    assert envelope["error"]["details"] == {"stage": "removal"}


def test_temporary_directory_inside_the_agent_environment(
    invoke: InvokeFn, environ: dict[str, str], tmp_path: Path
) -> None:
    inside = tmp_path / "tmp"
    inside.mkdir()
    code, envelope = sweep(invoke, {**environ, "TMPDIR": str(inside)})
    assert code == 1
    assert envelope["error"]["details"] == {
        "reason": "temporary_directory",
        "tool": None,
        "minimum_version": None,
    }


def test_truncated_listing(
    invoke: InvokeFn, env: dict[str, str], temp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workspace, "MAX_SWEEP_ENTRIES", 1)
    make(temp_root, name("a"), created=old())
    make(temp_root, name("b"), created=old())
    _, envelope = sweep(invoke, env)
    assert envelope["result"]["total"] == 2
    assert envelope["result"]["truncated"] is True
    assert len(envelope["result"]["workspaces"]) == 1


def test_a_full_listing_fits_the_envelope() -> None:
    entry = {
        "name": WORKSPACE_PREFIX + "f" * 32,
        "age_seconds": -(2**63),
        "eligible": False,
        "reason": "location",
    }
    result = {"workspaces": [entry] * MAX_SWEEP_ENTRIES, "total": 2**63, "truncated": True}
    raw = serialize(success("workspace sweep", result), "workspace sweep")
    assert check_document(raw)["status"] == "ok"

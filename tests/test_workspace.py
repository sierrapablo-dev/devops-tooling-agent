"""Workspaces: temporary directory, creation, marker, eligibility, and removal.

Policy 5.7.5. Nothing here starts a process.
"""

import contextlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from devops_tooling_agent import workspace
from devops_tooling_agent.constants import (
    MARKER_FILE_NAME,
    MARKER_MAX_BYTES,
    REMOVAL_ATTEMPTS,
    REMOVAL_RETRY_DELAY_SECONDS,
    SWEEP_MIN_AGE_SECONDS,
    WORKSPACE_PREFIX,
)
from devops_tooling_agent.errors import CommandError, ErrorCode

WINDOWS = sys.platform == "win32"
NOW = 1_800_000_000.0
OLD = NOW - SWEEP_MIN_AGE_SECONDS - 10


def name(suffix: str = "0") -> str:
    return WORKSPACE_PREFIX + suffix * 32


def make(
    root: Path, workspace_name: str, *, created: float = OLD, marker: bytes | None = None
) -> Path:
    path = root / workspace_name
    path.mkdir()
    data = workspace.marker_bytes(created) if marker is None else marker
    (path / MARKER_FILE_NAME).write_bytes(data)
    return path


def symlink_or_skip(link: Path, target: Path, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available")


# -- temporary directory ------------------------------------------------------------


def test_candidate_order(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    second.mkdir()
    agent = tmp_path / "agent"
    agent.mkdir()
    environ = {"TMPDIR": str(first), "TEMP": str(second), "TMP": str(tmp_path)}
    root = workspace.temporary_root(environ, windows=WINDOWS, cwd=str(agent))
    assert root == Path(os.path.realpath(second))


def test_relative_candidates_and_the_current_directory_are_never_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "relative").mkdir()
    agent = tmp_path / "agent"
    agent.mkdir()
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    environ = {"TMPDIR": "relative", "TEMP": str(fallback)}
    root = workspace.temporary_root(environ, windows=WINDOWS, cwd=str(agent))
    assert root == Path(os.path.realpath(fallback))


def test_resolution_writes_nothing(tmp_path: Path) -> None:
    temp = tmp_path / "temp"
    temp.mkdir()
    agent = tmp_path / "agent"
    agent.mkdir()
    workspace.temporary_root({"TMPDIR": str(temp)}, windows=WINDOWS, cwd=str(agent))
    assert list(temp.iterdir()) == []


def test_realpath_is_applied(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    symlink_or_skip(link, real, True)
    agent = tmp_path / "agent"
    agent.mkdir()
    root = workspace.temporary_root({"TMPDIR": str(link)}, windows=WINDOWS, cwd=str(agent))
    assert root == Path(os.path.realpath(real))


@pytest.mark.parametrize("inside", [True, False])
def test_temporary_directory_inside_the_agent_environment(tmp_path: Path, inside: bool) -> None:
    agent = tmp_path / "agent"
    temp = agent / "tmp" if inside else agent
    temp.mkdir(parents=True)
    with pytest.raises(CommandError) as raised:
        workspace.temporary_root({"TMPDIR": str(temp)}, windows=WINDOWS, cwd=str(agent))
    assert raised.value.code is ErrorCode.ENVIRONMENT_ERROR
    assert dict(raised.value.details) == {
        "reason": "temporary_directory",
        "tool": None,
        "minimum_version": None,
    }


def test_no_usable_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace, "_candidates", lambda environ, windows: [str(tmp_path / "x")])
    with pytest.raises(CommandError) as raised:
        workspace.temporary_root({}, windows=WINDOWS, cwd=str(tmp_path))
    assert raised.value.details["reason"] == "temporary_directory"


def test_windows_candidates() -> None:
    environ = {"userprofile": "C:\\Users\\u", "SystemRoot": "C:\\Windows", "Temp": "D:\\t"}
    candidates = workspace._candidates(environ, True)
    assert candidates == [
        "D:\\t",
        os.path.join("C:\\Users\\u", "AppData", "Local", "Temp"),
        os.path.join("C:\\Windows", "Temp"),
        "c:\\temp",
        "c:\\tmp",
    ]


def test_posix_candidates() -> None:
    expected = ["/x", "/tmp", "/var/tmp", "/usr/tmp"]  # noqa: S108  # expected candidates
    assert workspace._candidates({"TMPDIR": "/x"}, False) == expected


# -- creation and marker --------------------------------------------------------------


def test_create(tmp_path: Path) -> None:
    created = workspace.create(tmp_path, wall_clock=lambda: NOW)
    assert workspace.valid_name(created.name)
    assert created.path.is_dir()
    assert created.global_config.read_bytes() == b""
    content = json.loads(created.marker.read_bytes())
    assert list(content) == ["marker", "version", "created_at"]
    assert content["marker"] == "devops-tooling-agent-workspace"
    assert content["version"] == 1
    assert workspace.read_marker(created.path) == NOW
    assert len(created.marker.read_bytes()) < 120
    assert not created.repository.exists()
    if not WINDOWS:
        assert stat.S_IMODE(created.path.stat().st_mode) & 0o077 == 0


def test_names_are_random_and_valid() -> None:
    names = {workspace.new_name() for _ in range(100)}
    assert len(names) == 100 and all(workspace.valid_name(n) for n in names)


def test_create_retries_on_collision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    taken = name("a")
    (tmp_path / taken).mkdir()
    sequence = iter([taken, name("b")])
    monkeypatch.setattr(workspace, "new_name", lambda: next(sequence))
    created = workspace.create(tmp_path, wall_clock=lambda: NOW)
    assert created.name == name("b")
    assert list((tmp_path / taken).iterdir()) == []


def test_create_failure_removes_the_new_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(path: Path, data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(workspace, "_create_exclusive", failing)
    with pytest.raises(CommandError) as raised:
        workspace.create(tmp_path, wall_clock=lambda: NOW)
    assert raised.value.details["reason"] == "temporary_directory"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "value",
    [
        "devops-tooling-agent-" + "0" * 32,
        "devops-tooling-agent-" + "0123456789abcdef" * 2,
    ],
)
def test_valid_names(value: str) -> None:
    assert workspace.valid_name(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "devops-tooling-agent-",
        "devops-tooling-agent-" + "0" * 31,
        "devops-tooling-agent-" + "0" * 33,
        "devops-tooling-agent-" + "A" * 32,
        "devops-tooling-agent-" + "g" * 32,
        "../devops-tooling-agent-" + "0" * 29,
        "devops-tooling-agent-" + "0" * 31 + "/",
        "devops-tooling-agent-" + "0" * 31 + "\n",
        "devops-tooling-agent-" + "\u0660" * 32,
        "x" * 10000,
    ],
)
def test_invalid_names(value: str) -> None:
    assert not workspace.valid_name(value)


def marker(**changes: object) -> bytes:
    content: dict[str, object] = {
        "marker": "devops-tooling-agent-workspace",
        "version": 1,
        "created_at": "2026-01-01T00:00:00Z",
    }
    content.update(changes)
    return json.dumps({k: v for k, v in content.items() if v is not ...}).encode()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json",
        b"[]",
        b"\xff\xfe",
        marker(marker="other"),
        marker(version=2),
        marker(version=True),
        marker(version="1"),
        marker(created_at=None),
        marker(created_at=1767225600),
        marker(created_at="2026-01-01 00:00:00Z"),
        marker(created_at="2026-02-30T00:00:00Z"),
        marker(created_at="2026-01-01T00:00:00+00:00"),
        marker(created_at="2026-01-01T00:00:00Z" + "x" * 10),
        marker(created_at=...),
        marker(extra=1),
        marker(created_at="2026-01-01T00:00:00Z") + b" " * MARKER_MAX_BYTES,
        b"[" * 100000,
    ],
)
def test_invalid_markers_never_raise(tmp_path: Path, data: bytes) -> None:
    path = make(tmp_path, name(), marker=data)
    assert workspace.read_marker(path) is None
    entry = workspace.inspect(tmp_path, name(), NOW)
    assert entry == workspace.Entry(name(), None, False, "marker")


def test_marker_that_is_a_directory_or_missing(tmp_path: Path) -> None:
    (tmp_path / name("1")).mkdir()
    assert workspace.inspect(tmp_path, name("1"), NOW).reason == "marker"
    (tmp_path / name("2") / MARKER_FILE_NAME).mkdir(parents=True)
    assert workspace.inspect(tmp_path, name("2"), NOW).reason == "marker"


def test_marker_that_is_a_link(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_bytes(workspace.marker_bytes(OLD))
    path = tmp_path / name()
    path.mkdir()
    symlink_or_skip(path / MARKER_FILE_NAME, target, False)
    assert workspace.inspect(tmp_path, name(), NOW).reason == "marker"


# -- eligibility -------------------------------------------------------------------


def test_eligible(tmp_path: Path) -> None:
    make(tmp_path, name())
    assert workspace.inspect(tmp_path, name(), NOW) == workspace.Entry(
        name(), SWEEP_MIN_AGE_SECONDS + 10, True, None
    )


@pytest.mark.parametrize(
    ("created", "age"),
    [
        (NOW - SWEEP_MIN_AGE_SECONDS, SWEEP_MIN_AGE_SECONDS),
        (NOW - 5, 5),
        (NOW + 3600, -3600),
    ],
)
def test_age(tmp_path: Path, created: float, age: int) -> None:
    make(tmp_path, name(), created=created)
    assert workspace.inspect(tmp_path, name(), NOW) == workspace.Entry(name(), age, False, "age")


def test_name_is_checked_before_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("filesystem access")

    monkeypatch.setattr(os, "lstat", forbidden)
    assert workspace.inspect(tmp_path, "../" + name(), NOW).reason == "name"


def test_not_found(tmp_path: Path) -> None:
    assert workspace.inspect(tmp_path, name(), NOW).reason == "not_found"
    (tmp_path / name("1")).write_bytes(b"a regular file")
    assert workspace.inspect(tmp_path, name("1"), NOW).reason == "not_found"


def test_symlinked_workspace_is_a_link(tmp_path: Path) -> None:
    target = make(tmp_path, "real-target")
    symlink_or_skip(tmp_path / name(), target, True)
    entry = workspace.inspect(tmp_path, name(), NOW)
    assert entry == workspace.Entry(name(), None, False, "link")


@pytest.mark.skipif(not WINDOWS, reason="Windows junctions")
def test_junction_is_a_link(tmp_path: Path) -> None:
    import _winapi

    target = make(tmp_path, "junction-target")
    _winapi.CreateJunction(str(target), str(tmp_path / name()))  # type: ignore[attr-defined]
    assert workspace.inspect(tmp_path, name(), NOW).reason == "link"


def test_location_on_another_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make(tmp_path, name())
    real_stat = os.stat

    def other_device(path: object, *args: object, **kwargs: object) -> os.stat_result:
        status = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(str(path)) == tmp_path:
            values = list(status)
            values[stat.ST_DEV] = status.st_dev + 1
            return os.stat_result(values)
        return status

    monkeypatch.setattr(os, "stat", other_device)
    assert workspace.inspect(tmp_path, name(), NOW).reason == "location"


def test_location_outside_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make(tmp_path, name())
    monkeypatch.setattr(os.path, "realpath", lambda path: str(tmp_path / "elsewhere" / name()))
    assert workspace.inspect(tmp_path, name(), NOW).reason == "location"


# -- listing and stale detection -------------------------------------------------------


def test_listing(tmp_path: Path) -> None:
    make(tmp_path, name("b"))
    make(tmp_path, name("a"), created=NOW)
    (tmp_path / (WORKSPACE_PREFIX + "bad")).mkdir()
    (tmp_path / (WORKSPACE_PREFIX + "file")).write_bytes(b"")
    (tmp_path / "unrelated").mkdir()
    entries, total = workspace.list_workspaces(tmp_path, NOW)
    assert total == 3
    # Sorted by name bytes: "...aaaa" < "...bad" < "...bbbb".
    assert [entry.to_result() for entry in entries] == [
        {"name": name("a"), "age_seconds": 0, "eligible": False, "reason": "age"},
        {"name": None, "age_seconds": None, "eligible": False, "reason": "name"},
        {
            "name": name("b"),
            "age_seconds": SWEEP_MIN_AGE_SECONDS + 10,
            "eligible": True,
            "reason": None,
        },
    ]
    assert (tmp_path / name("b")).exists()


def test_listing_includes_links(tmp_path: Path) -> None:
    target = make(tmp_path, "target")
    symlink_or_skip(tmp_path / name(), target, True)
    entries, total = workspace.list_workspaces(tmp_path, NOW)
    assert total == 1 and entries[0].reason == "link"


def test_listing_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workspace, "MAX_SWEEP_ENTRIES", 2)
    for suffix in "abc":
        make(tmp_path, name(suffix))
    entries, total = workspace.list_workspaces(tmp_path, NOW)
    assert total == 3
    assert [entry.name for entry in entries] == [name("a"), name("b")]


def test_detect_stale(tmp_path: Path) -> None:
    make(tmp_path, name("a"))
    make(tmp_path, name("b"), created=NOW)
    (tmp_path / (WORKSPACE_PREFIX + "bad")).mkdir()
    assert workspace.detect_stale(tmp_path, own=name("a")) == [name("b")]
    assert workspace.detect_stale(tmp_path) == [name("a"), name("b")]
    assert (tmp_path / name("a")).exists() and (tmp_path / name("b")).exists()


# -- removal ---------------------------------------------------------------------------


def no_sleep(seconds: float) -> None:
    pass


def test_removal_with_read_only_files(tmp_path: Path) -> None:
    path = make(tmp_path, name())
    pack = path / "repository.git" / "objects" / "pack"
    pack.mkdir(parents=True)
    for file_name in ("pack-1.pack", "pack-1.idx"):
        (pack / file_name).write_bytes(b"x")
        (pack / file_name).chmod(stat.S_IREAD)
    (path / MARKER_FILE_NAME).chmod(stat.S_IREAD)
    assert workspace.remove(path, validate=lambda: True, sleep=no_sleep)
    assert not path.exists()


@pytest.mark.skipif(WINDOWS, reason="POSIX directory permissions")
def test_removal_with_read_only_directories(tmp_path: Path) -> None:
    path = make(tmp_path, name())
    locked = path / "locked"
    locked.mkdir()
    (locked / "file").write_bytes(b"x")
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    assert workspace.remove(path, validate=lambda: True, sleep=no_sleep)
    assert not path.exists()


def test_removal_never_follows_links(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    path = make(tmp_path, name())
    symlink_or_skip(path / "dir-link", outside, True)
    symlink_or_skip(path / "file-link", outside / "keep.txt", False)
    assert workspace.remove(path, validate=lambda: True, sleep=no_sleep)
    assert not path.exists()
    assert (outside / "keep.txt").read_bytes() == b"keep"


@pytest.mark.skipif(not WINDOWS, reason="Windows junctions")
def test_removal_never_follows_junctions(tmp_path: Path) -> None:
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    path = make(tmp_path, name())
    _winapi.CreateJunction(str(outside), str(path / "junction"))  # type: ignore[attr-defined]
    assert workspace.remove(path, validate=lambda: True, sleep=no_sleep)
    assert not path.exists()
    assert (outside / "keep.txt").read_bytes() == b"keep"


def test_removal_is_refused_when_validation_fails(tmp_path: Path) -> None:
    path = make(tmp_path, name())
    assert not workspace.remove(path, validate=lambda: False, sleep=no_sleep)
    assert (path / MARKER_FILE_NAME).exists()


def test_bounded_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make(tmp_path, name())
    (path / "held").write_bytes(b"x")
    sleeps: list[float] = []
    real_unlink = os.unlink

    def held(target: object, *args: object, **kwargs: object) -> None:
        if str(target).endswith("held"):
            raise PermissionError("held by another process")
        real_unlink(target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", held)
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)
    assert not workspace.remove(path, validate=lambda: True, sleep=sleeps.append)
    assert sleeps == [REMOVAL_RETRY_DELAY_SECONDS] * (REMOVAL_ATTEMPTS - 1)
    # The marker is removed last, so the workspace is still detectable.
    assert workspace.read_marker(path) is not None


def test_retry_succeeds_after_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make(tmp_path, name())
    (path / "held").write_bytes(b"x")
    # Each attempt tries twice: once, and again after making the file writable.
    failures = [4]
    real_unlink = os.unlink

    def held(target: object, *args: object, **kwargs: object) -> None:
        if str(target).endswith("held") and failures[0]:
            failures[0] -= 1
            raise PermissionError("held")
        real_unlink(target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "unlink", held)
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)
    sleeps: list[float] = []
    assert workspace.remove(path, validate=lambda: True, sleep=sleeps.append)
    assert len(sleeps) == 2 and not path.exists()


def test_retry_after_the_marker_was_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = make(tmp_path, name())
    real_rmdir = os.rmdir
    failures = [1]

    def flaky(target: object, *args: object, **kwargs: object) -> None:
        if Path(str(target)) == path and failures[0]:
            failures[0] -= 1
            raise PermissionError("busy")
        real_rmdir(target, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "rmdir", flaky)
    validations: list[int] = []

    def validate() -> bool:
        validations.append(1)
        return True

    assert workspace.remove(path, validate=validate, sleep=no_sleep)
    assert len(validations) == 1 and not path.exists()


def test_size_bytes(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"x" * 10)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b").write_bytes(b"x" * 5)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "big").write_bytes(b"x" * 1000)
    with contextlib.suppress(OSError, NotImplementedError):
        os.symlink(outside, tmp_path / "link", target_is_directory=True)
    assert workspace.size_bytes(tmp_path) == 15

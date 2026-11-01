"""Ephemeral workspaces (ADR-0006 Section 10, security policy Section 5.7.5).

A workspace is a private directory created directly under the system temporary
directory, with the workspace prefix, a random suffix, and an ownership marker.
Nothing here starts a process. Links, junctions, and other reparse points are
never followed: they are reported, refused, or removed as links.
"""

import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from devops_tooling_agent.constants import (
    GLOBAL_CONFIG_FILE_NAME,
    MARKER_FILE_NAME,
    MARKER_ID,
    MARKER_MAX_BYTES,
    MARKER_VERSION,
    MAX_SWEEP_ENTRIES,
    REMOVAL_ATTEMPTS,
    REMOVAL_RETRY_DELAY_SECONDS,
    REPOSITORY_DIR_NAME,
    SWEEP_MIN_AGE_SECONDS,
    WORKSPACE_PREFIX,
    WORKSPACE_SUFFIX_CHARS,
)
from devops_tooling_agent.errors import CommandError, ErrorCode

NAME_LENGTH: Final = len(WORKSPACE_PREFIX) + WORKSPACE_SUFFIX_CHARS
_NAME: Final = re.compile(
    re.escape(WORKSPACE_PREFIX) + f"[0-9a-f]{{{WORKSPACE_SUFFIX_CHARS}}}", re.ASCII
)
_CREATED_AT: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)
_CREATED_AT_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"
_CREATE_ATTEMPTS: Final = 8
_MARKER_KEYS: Final = frozenset({"marker", "version", "created_at"})
_BINARY: Final = getattr(os, "O_BINARY", 0)
_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)

# Eligibility reasons (docs/python-package.md, Section 3), in evaluation order.
REASON_NAME: Final = "name"
REASON_NOT_FOUND: Final = "not_found"
REASON_LINK: Final = "link"
REASON_LOCATION: Final = "location"
REASON_MARKER: Final = "marker"
REASON_AGE: Final = "age"

Clock = Callable[[], float]


def environment_error() -> CommandError:
    return CommandError(
        ErrorCode.ENVIRONMENT_ERROR,
        {"reason": "temporary_directory", "tool": None, "minimum_version": None},
    )


def _lookup(environ: Mapping[str, str], name: str, windows: bool) -> str | None:
    if not windows:
        return environ.get(name)
    for key, value in environ.items():
        if key.upper() == name:
            return value
    return None


def _is_absolute(path: str, windows: bool) -> bool:
    if windows:
        drive, rest = os.path.splitdrive(path)
        return bool(drive) and rest.startswith(("\\", "/"))
    return path.startswith("/")


def _candidates(environ: Mapping[str, str], windows: bool) -> list[str]:
    """The candidate order of ``tempfile``, without its current directory fallback."""
    result = [
        value for name in ("TMPDIR", "TEMP", "TMP") if (value := _lookup(environ, name, windows))
    ]
    if windows:
        profile = _lookup(environ, "USERPROFILE", windows)
        if not profile:
            drive = _lookup(environ, "HOMEDRIVE", windows) or ""
            profile = drive + (_lookup(environ, "HOMEPATH", windows) or "")
        if profile:
            result.append(os.path.join(profile, "AppData", "Local", "Temp"))
        system_root = _lookup(environ, "SYSTEMROOT", windows)
        if system_root:
            result.append(os.path.join(system_root, "Temp"))
        # tempfile also tries \temp and \tmp, which depend on the current drive.
        result += ["c:\\temp", "c:\\tmp"]
    else:
        # The fallback locations of tempfile. They are only candidates for the
        # system temporary directory; workspaces are created in them exclusively,
        # with mode 0o700 and a random name.
        result += ["/tmp", "/var/tmp"]  # noqa: S108  # nosec B108
        result.append("/usr/tmp")
    return result


def _normalized(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _inside(path: Path, directory: Path) -> bool:
    a, b = _normalized(path), _normalized(directory)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def temporary_root(environ: Mapping[str, str], *, windows: bool, cwd: str) -> Path:
    """Resolve the system temporary directory without writing anything.

    The first candidate that is absolute, resolves to an existing directory,
    and is writable is used, after ``realpath``. Raises ``ENVIRONMENT_ERROR``
    when none is usable or the directory is inside the agent environment, which
    is the working directory of the invocation (ADR-0007 Section 1).
    """
    for candidate in _candidates(environ, windows):
        if not _is_absolute(candidate, windows):
            continue
        real = os.path.realpath(candidate)
        if not os.path.isdir(real) or not os.access(real, os.W_OK | os.X_OK):
            continue
        root = Path(real)
        agent = Path(os.path.realpath(cwd))
        if _inside(root, agent):
            raise environment_error()
        return root
    raise environment_error()


def valid_name(name: str) -> bool:
    """Strict workspace name validation. Uses no filesystem access."""
    return len(name) == NAME_LENGTH and _NAME.fullmatch(name) is not None


def new_name() -> str:
    return WORKSPACE_PREFIX + secrets.token_hex(WORKSPACE_SUFFIX_CHARS // 2)


@dataclass(frozen=True)
class Workspace:
    root: Path
    name: str

    @property
    def path(self) -> Path:
        return self.root / self.name

    @property
    def marker(self) -> Path:
        return self.path / MARKER_FILE_NAME

    @property
    def global_config(self) -> Path:
        return self.path / GLOBAL_CONFIG_FILE_NAME

    @property
    def repository(self) -> Path:
        return self.path / REPOSITORY_DIR_NAME


def format_created_at(timestamp: float) -> str:
    return datetime.fromtimestamp(int(timestamp), UTC).strftime(_CREATED_AT_FORMAT)


def marker_bytes(timestamp: float) -> bytes:
    content = {
        "marker": MARKER_ID,
        "version": MARKER_VERSION,
        "created_at": format_created_at(timestamp),
    }
    return (json.dumps(content) + "\n").encode("ascii")


def _create_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY | _NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)


def create(root: Path, *, wall_clock: Clock) -> Workspace:
    """Create a private workspace, its marker, and an empty Git global configuration.

    The directory is created exclusively with mode 0o700, which on Windows also
    restricts it to the current user (Python 3.12.4 and later). The marker is
    created exclusively before any Git command. If the marker or the
    configuration file cannot be written, the new directory is removed again.
    """
    for _ in range(_CREATE_ATTEMPTS):
        workspace = Workspace(root, new_name())
        try:
            os.mkdir(workspace.path, 0o700)
        except FileExistsError:
            continue
        except OSError:
            raise environment_error() from None
        try:
            _create_exclusive(workspace.marker, marker_bytes(wall_clock()))
            _create_exclusive(workspace.global_config, b"")
        except OSError:
            for leftover in (workspace.global_config, workspace.marker):
                try:
                    os.unlink(leftover)
                except FileNotFoundError:
                    pass
                except OSError:
                    break
            try:
                os.rmdir(workspace.path)
            except OSError:
                raise CommandError(
                    ErrorCode.CLEANUP_FAILED,
                    {"workspace": workspace.name, "prior_error": ErrorCode.ENVIRONMENT_ERROR.name},
                ) from None
            raise environment_error() from None
        return workspace
    raise environment_error()


def is_link(status: os.stat_result) -> bool:
    """A symbolic link, a junction, or any other reparse point."""
    if stat.S_ISLNK(status.st_mode):
        return True
    attributes = getattr(status, "st_file_attributes", 0)
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def read_marker(directory: Path) -> float | None:
    """The creation time of a valid marker, or ``None``. Never raises."""
    try:
        marker = directory / MARKER_FILE_NAME
        status = os.lstat(marker)
        if is_link(status) or not stat.S_ISREG(status.st_mode):
            return None
        if status.st_size > MARKER_MAX_BYTES:
            return None
        descriptor = os.open(marker, os.O_RDONLY | _BINARY | _NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                return None
            data = os.read(descriptor, MARKER_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(data) > MARKER_MAX_BYTES:
            return None
        content = json.loads(data.decode("ascii"))
        if not isinstance(content, dict) or set(content) != _MARKER_KEYS:
            return None
        version = content["version"]
        created_at = content["created_at"]
        if content["marker"] != MARKER_ID:
            return None
        if not isinstance(version, int) or isinstance(version, bool) or version != MARKER_VERSION:
            return None
        if not isinstance(created_at, str) or not _CREATED_AT.fullmatch(created_at):
            return None
        parsed = datetime.strptime(created_at, _CREATED_AT_FORMAT).replace(tzinfo=UTC)
        return parsed.timestamp()
    except (OSError, ValueError, UnicodeDecodeError, RecursionError, OverflowError):
        return None


@dataclass(frozen=True)
class Entry:
    """Eligibility of one workspace for ``workspace sweep --remove``."""

    name: str
    age_seconds: int | None
    eligible: bool
    reason: str | None

    def to_result(self) -> dict[str, object]:
        # A name that does not match the workspace pattern is never reported.
        return {
            "name": self.name if valid_name(self.name) else None,
            "age_seconds": self.age_seconds,
            "eligible": self.eligible,
            "reason": self.reason,
        }


def _directly_under(path: Path, root: Path, status: os.stat_result) -> bool:
    try:
        real = Path(os.path.realpath(path))
        root_status = os.stat(root)
    except OSError:
        return False
    if _normalized(real.parent) != _normalized(root) or real.name != path.name:
        return False
    return status.st_dev == root_status.st_dev


def _own_checks(root: Path, name: str) -> tuple[str | None, float | None]:
    """The checks shared by eligibility and own-workspace removal, in order."""
    if not valid_name(name):
        return REASON_NAME, None
    path = root / name
    try:
        status = os.lstat(path)
    except OSError:
        return REASON_NOT_FOUND, None
    if is_link(status):
        return REASON_LINK, None
    if not stat.S_ISDIR(status.st_mode):
        return REASON_NOT_FOUND, None
    if not _directly_under(path, root, status):
        return REASON_LOCATION, None
    created = read_marker(path)
    if created is None:
        return REASON_MARKER, None
    return None, created


def inspect(root: Path, name: str, now: float) -> Entry:
    """Evaluate eligibility (security policy Section 5.7.5). Never raises."""
    reason, created = _own_checks(root, name)
    if reason is not None or created is None:
        return Entry(name, None, False, reason)
    age = now - created
    age_seconds = int(age // 1)
    if age <= SWEEP_MIN_AGE_SECONDS:
        return Entry(name, age_seconds, False, REASON_AGE)
    return Entry(name, age_seconds, True, None)


def owned(workspace: Workspace) -> bool:
    """Re-validate the invocation's own workspace before removal."""
    reason, _ = _own_checks(workspace.root, workspace.name)
    return reason is None


def _sort_key(name: str) -> bytes:
    return name.encode("utf-8", "surrogateescape")


def _prefixed(root: Path) -> list[str]:
    names: list[str] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if not entry.name.startswith(WORKSPACE_PREFIX):
                continue
            try:
                status = os.lstat(entry.path)
            except OSError:
                continue
            if is_link(status) or stat.S_ISDIR(status.st_mode):
                names.append(entry.name)
    return sorted(names, key=_sort_key)


def list_workspaces(root: Path, now: float) -> tuple[list[Entry], int]:
    """Every directory or link with the workspace prefix, at most ``MAX_SWEEP_ENTRIES``.

    Returns the evaluated entries and the total number found. Removes nothing.
    """
    try:
        names = _prefixed(root)
    except OSError:
        raise environment_error() from None
    return [inspect(root, name, now) for name in names[:MAX_SWEEP_ENTRIES]], len(names)


def detect_stale(root: Path, *, own: str | None = None) -> list[str]:
    """Names of workspaces of other invocations, for a warning. Removes nothing."""
    try:
        names = _prefixed(root)
    except OSError:
        raise environment_error() from None
    return [name for name in names if valid_name(name) and name != own]


def _make_writable(path: str, status: os.stat_result) -> None:
    if stat.S_ISDIR(status.st_mode):
        os.chmod(path, stat.S_IMODE(status.st_mode) | stat.S_IRWXU)
    else:
        os.chmod(path, stat.S_IMODE(status.st_mode) | stat.S_IWRITE)


def _remove_entry(path: str, remove: Callable[[str], None], status: os.stat_result) -> None:
    try:
        remove(path)
    except PermissionError:
        # Read-only files on Windows, and read-only directories.
        _make_writable(path, status)
        remove(path)


def _clear(directory: str, *, keep: str | None) -> None:
    """Remove the contents of a real directory. Links are removed, never followed."""
    with os.scandir(directory) as iterator:
        entries = list(iterator)
    for entry in entries:
        if keep is not None and entry.name == keep:
            continue
        status = os.lstat(entry.path)
        if is_link(status):
            # os.unlink removes a symbolic link or junction itself, including a
            # directory link on Windows, and never its target.
            os.unlink(entry.path)
        elif stat.S_ISDIR(status.st_mode):
            if status.st_mode & stat.S_IRWXU != stat.S_IRWXU:
                _make_writable(entry.path, status)
            _clear(entry.path, keep=None)
            _remove_entry(entry.path, os.rmdir, status)
        else:
            _remove_entry(entry.path, os.unlink, status)


def remove(
    path: Path,
    *,
    validate: Callable[[], bool],
    sleep: Callable[[float], None],
    attempts: int = REMOVAL_ATTEMPTS,
    delay: float = REMOVAL_RETRY_DELAY_SECONDS,
) -> bool:
    """Remove a workspace with bounded retries. Returns whether it is gone.

    ``validate`` re-checks the workspace before each attempt, while its marker
    exists. The marker is removed last, so that a partial removal leaves a
    workspace that stale detection still reports.
    """
    marker_removed = False
    for attempt in range(attempts):
        if attempt:
            sleep(delay)
        if not marker_removed and not validate():
            return False
        try:
            if not marker_removed:
                _clear(str(path), keep=MARKER_FILE_NAME)
                marker = str(path / MARKER_FILE_NAME)
                _remove_entry(marker, os.unlink, os.lstat(marker))
                marker_removed = True
            else:
                _clear(str(path), keep=None)
            os.rmdir(path)
            return True
        except OSError:
            continue
    return False


def size_bytes(path: Path) -> int:
    """Total size of the regular files under ``path``, without following links.

    Each file is measured with ``lstat`` rather than the cached directory entry,
    which can lag behind a file that is being written on Windows.
    """
    total = 0
    pending = [str(path)]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                names = [entry.path for entry in iterator]
        except OSError:
            continue
        for entry_path in names:
            try:
                status = os.lstat(entry_path)
            except OSError:
                continue
            if is_link(status):
                continue
            if stat.S_ISDIR(status.st_mode):
                pending.append(entry_path)
            elif stat.S_ISREG(status.st_mode):
                total += status.st_size
    return total

"""Subprocess environment and executable resolution.

The environment of every subprocess is built from scratch from the allowlist of
security policy Section 5.7.4, plus fixed values. Executables are resolved once
to absolute paths by walking the allowlisted ``PATH``, never searching the
current directory.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

# Security policy Section 5.7.4: variables passed through unchanged when set.
PASS_THROUGH: Final[tuple[str, ...]] = (
    # Process execution
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    # User profile and OS keyring
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "USERNAME",
    "USER",
    "LOGNAME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    # Temporary directory
    "TMPDIR",
    "TEMP",
    "TMP",
    # HTTPS proxy
    "HTTPS_PROXY",
    "https_proxy",
    "NO_PROXY",
    "no_proxy",
    # TLS trust
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    # gh configuration location
    "GH_CONFIG_DIR",
)

# Security policy Section 5.7.4: fixed values for every subprocess.
FIXED_ALL: Final[Mapping[str, str]] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "NO_COLOR": "1",
    "LC_ALL": "C",
}

# Only real executables are accepted on Windows. Batch files run through
# cmd.exe, which is an interpreter wrapper (security policy Section 5.7.3).
_WINDOWS_EXTENSIONS: Final = frozenset({".EXE"})
_DEFAULT_PATHEXT: Final = ".EXE"


def build_environment(
    source: Mapping[str, str],
    *,
    windows: bool,
    fixed: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a subprocess environment from ``source``.

    Only allowlisted variables are copied, then the fixed values for every
    subprocess, then ``fixed``, the values for a specific tool (Git adds its own
    in #17). Fixed values always win. On Windows, variable names are matched
    case-insensitively, as the operating system does.
    """
    env: dict[str, str] = {}
    if windows:
        by_upper: dict[str, str] = {}
        for name, value in source.items():
            by_upper.setdefault(name.upper(), value)
        for name in PASS_THROUGH:
            upper = name.upper()
            if upper in by_upper and upper not in {key.upper() for key in env}:
                env[name] = by_upper[upper]
    else:
        for name in PASS_THROUGH:
            if name in source:
                env[name] = source[name]
    env.update(FIXED_ALL)
    if fixed is not None:
        env.update(fixed)
    return env


def _same_directory(entry: str, cwd: str, windows: bool) -> bool:
    a = os.path.normpath(entry)
    b = os.path.normpath(cwd)
    if windows:
        return a.lower() == b.lower()
    return a == b


def _search_path(env: Mapping[str, str], windows: bool) -> list[str]:
    for name, value in env.items():
        if name == "PATH" or (windows and name.upper() == "PATH"):
            return value.split(";" if windows else ":")
    return []


def _extensions(env: Mapping[str, str], windows: bool) -> list[str]:
    if not windows:
        return [""]
    raw = _DEFAULT_PATHEXT
    for name, value in env.items():
        if name.upper() == "PATHEXT":
            raw = value
            break
    result: list[str] = []
    for ext in raw.split(";"):
        upper = ext.strip().upper()
        if upper in _WINDOWS_EXTENSIONS and upper not in result:
            result.append(upper)
    return result


def _is_absolute(entry: str, windows: bool) -> bool:
    if windows:
        # A drive-relative path such as "C:bin" or a rooted path such as "\bin"
        # is not absolute.
        drive, rest = os.path.splitdrive(entry)
        return bool(drive) and rest.startswith(("\\", "/"))
    return entry.startswith("/")


def resolve_executable(
    name: str,
    env: Mapping[str, str],
    *,
    cwd: str,
    windows: bool,
) -> Path | None:
    """Resolve ``name`` to an absolute executable path through ``PATH``.

    Empty, relative, and current-directory entries are skipped. On Windows,
    ``PATHEXT`` gives the order of extensions, and only ``.EXE`` is accepted.
    Returns ``None`` when nothing qualifies.
    """
    for raw_entry in _search_path(env, windows):
        entry = raw_entry.strip().strip('"') if windows else raw_entry
        if not entry or not _is_absolute(entry, windows):
            continue
        if _same_directory(entry, cwd, windows):
            continue
        for ext in _extensions(env, windows):
            candidate = Path(entry) / f"{name}{ext.lower() if windows else ext}"
            if not candidate.is_file():
                continue
            if not windows and not os.access(candidate, os.X_OK):
                continue
            return candidate
    return None

"""Subprocess environment allowlist and executable resolution (policy 5.7.3, 5.7.4)."""

import os
import sys
from pathlib import Path

import pytest

from devops_tooling_agent.environment import (
    FIXED_ALL,
    PASS_THROUGH,
    build_environment,
    resolve_executable,
)

WINDOWS = sys.platform == "win32"

DROPPED = [
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_ENTERPRISE_TOKEN",
    "GH_HOST",
    "GH_REPO",
    "GH_DEBUG",
    "DEBUG",
    "GH_PAGER",
    "PAGER",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_KEY_0",
    "GIT_CONFIG_VALUE_0",
    "GIT_CONFIG_PARAMETERS",
    "GIT_SSH_COMMAND",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_TEMPLATE_DIR",
    "GIT_EXEC_PATH",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_TRACE",
    "GIT_TRACE_PACKET",
    "GIT_SSL_NO_VERIFY",
    "GIT_SSL_CAINFO",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "PYTHONPATH",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "HTTP_PROXY",
    "ALL_PROXY",
    "SOME_OTHER_VARIABLE",
]

EXPECTED_FIXED = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "NO_COLOR": "1",
    "LC_ALL": "C",
}


def test_allowlist_matches_policy() -> None:
    assert set(PASS_THROUGH) == {
        "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA",
        "PROGRAMDATA", "USERNAME", "USER", "LOGNAME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
        "TMPDIR", "TEMP", "TMP",
        "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR",
        "GH_CONFIG_DIR",
    }  # fmt: skip
    assert dict(FIXED_ALL) == EXPECTED_FIXED
    assert not any(name.upper().startswith("GIT_") for name in PASS_THROUGH)
    assert not any("TOKEN" in name.upper() for name in PASS_THROUGH)


@pytest.mark.parametrize("windows", [False, True])
def test_allowlisted_variables_pass_and_others_are_dropped(windows: bool) -> None:
    source = {name: f"value-{index}" for index, name in enumerate(PASS_THROUGH)}
    source.update({name: "dropped" for name in DROPPED})
    source.update({"GIT_TERMINAL_PROMPT": "1", "GCM_INTERACTIVE": "always", "LC_ALL": "en"})
    env = build_environment(source, windows=windows)
    for name in DROPPED:
        if name not in EXPECTED_FIXED:
            assert name not in env
    for name, value in EXPECTED_FIXED.items():
        assert env[name] == value
    assert not {key for key in env if key.startswith("GIT_")} - set(EXPECTED_FIXED)
    assert "dropped" not in env.values()
    if not windows:
        for name in PASS_THROUGH:
            assert env[name] == source[name]


def test_windows_matching_is_case_insensitive() -> None:
    source = {"Path": "C:\\bin", "SystemRoot": "C:\\Windows", "gh_token": "x", "Https_Proxy": "p"}
    env = build_environment(source, windows=True)
    assert env["PATH"] == "C:\\bin"
    assert env["SYSTEMROOT"] == "C:\\Windows"
    assert list(env.values()).count("p") == 1
    assert all(key.upper() != "GH_TOKEN" for key in env)


def test_posix_matching_is_case_sensitive() -> None:
    env = build_environment({"Path": "/bin", "https_proxy": "p"}, windows=False)
    assert "PATH" not in env and "Path" not in env
    assert env["https_proxy"] == "p"


def test_tool_specific_fixed_values() -> None:
    env = build_environment(
        {"GIT_CONFIG_NOSYSTEM": "0"}, windows=False, fixed={"GIT_CONFIG_NOSYSTEM": "1"}
    )
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"


def test_source_mapping_is_not_modified() -> None:
    source = {"PATH": "/bin", "GH_TOKEN": "x"}
    build_environment(source, windows=False)
    assert source == {"PATH": "/bin", "GH_TOKEN": "x"}


# Executable resolution -------------------------------------------------------


def make_executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"")
    path.chmod(0o755)
    return path


def gh_name() -> str:
    return "gh.exe" if WINDOWS else "gh"


def env_with(entries: list[str]) -> dict[str, str]:
    env = {"PATH": os.pathsep.join(entries)}
    if WINDOWS:
        env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    return env


def test_resolves_first_absolute_match(tmp_path: Path) -> None:
    first = make_executable(tmp_path / "first", gh_name())
    make_executable(tmp_path / "second", gh_name())
    env = env_with([str(tmp_path / "missing"), str(first.parent), str(tmp_path / "second")])
    found = resolve_executable("gh", env, cwd=str(tmp_path / "elsewhere"), windows=WINDOWS)
    assert found == first


def test_skips_empty_relative_and_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    make_executable(cwd, gh_name())
    make_executable(cwd / "rel", gh_name())
    monkeypatch.chdir(cwd)
    env = env_with(["", ".", "rel", "./rel", str(cwd), str(cwd) + os.sep])
    assert resolve_executable("gh", env, cwd=str(cwd), windows=WINDOWS) is None

    elsewhere = make_executable(tmp_path / "real", gh_name())
    env = env_with(["", ".", "rel", str(cwd), str(elsewhere.parent)])
    assert resolve_executable("gh", env, cwd=str(cwd), windows=WINDOWS) == elsewhere


def test_not_found(tmp_path: Path) -> None:
    assert resolve_executable("gh", env_with([str(tmp_path)]), cwd="/", windows=WINDOWS) is None
    assert resolve_executable("gh", {}, cwd="/", windows=WINDOWS) is None


@pytest.mark.skipif(not WINDOWS, reason="Windows executable extensions")
def test_windows_accepts_only_exe(tmp_path: Path) -> None:
    for name in ("gh.cmd", "gh.bat", "gh.com", "gh"):
        make_executable(tmp_path / "shims", name)
    env = env_with([str(tmp_path / "shims")])
    assert resolve_executable("gh", env, cwd="C:\\", windows=True) is None
    exe = make_executable(tmp_path / "real", "gh.exe")
    env = env_with([str(tmp_path / "shims"), str(exe.parent)])
    assert resolve_executable("gh", env, cwd="C:\\", windows=True) == exe


@pytest.mark.skipif(not WINDOWS, reason="Windows path forms")
def test_windows_skips_drive_relative_and_rooted_entries(tmp_path: Path) -> None:
    make_executable(tmp_path / "bin", "gh.exe")
    drive_relative = tmp_path.drive + "bin"
    rooted = str(tmp_path / "bin")[len(tmp_path.drive) :]
    env = {"PATH": f"{drive_relative};{rooted}", "PATHEXT": ".EXE"}
    assert resolve_executable("gh", env, cwd="C:\\", windows=True) is None


@pytest.mark.skipif(not WINDOWS, reason="Windows PATHEXT")
def test_windows_without_exe_in_pathext(tmp_path: Path) -> None:
    make_executable(tmp_path / "bin", "gh.exe")
    env = {"PATH": str(tmp_path / "bin"), "PATHEXT": ".CMD;.BAT"}
    assert resolve_executable("gh", env, cwd="C:\\", windows=True) is None


@pytest.mark.skipif(WINDOWS, reason="POSIX permission bits")
def test_posix_requires_execute_permission(tmp_path: Path) -> None:
    path = make_executable(tmp_path / "bin", "gh")
    path.chmod(0o644)
    env = {"PATH": str(path.parent)}
    assert resolve_executable("gh", env, cwd="/", windows=False) is None
    path.chmod(0o755)
    assert resolve_executable("gh", env, cwd="/", windows=False) == path


@pytest.mark.skipif(WINDOWS, reason="POSIX path forms")
def test_posix_ignores_directories_named_like_the_executable(tmp_path: Path) -> None:
    (tmp_path / "bin" / "gh").mkdir(parents=True)
    env = {"PATH": str(tmp_path / "bin")}
    assert resolve_executable("gh", env, cwd="/", windows=False) is None

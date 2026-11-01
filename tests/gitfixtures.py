"""Local Git fixtures for lifecycle tests (ADR-0007 Section 9).

Fixtures are local bare repositories reached through ``file://``. They are
created with an isolated, empty configuration, so the operator's Git
configuration is neither read nor changed. No network access is involved.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WINDOWS = sys.platform == "win32"
GIT = shutil.which("git")
requires_git = pytest.mark.skipif(GIT is None, reason="git is not installed")

_KEEP = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP"}
IDENTITY = ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]


def fixture_env(base: Path) -> dict[str, str]:
    home = base / "fixture-home"
    home.mkdir(exist_ok=True)
    empty = home / "empty.gitconfig"
    if not empty.exists():
        empty.write_bytes(b"")
    env = {key: value for key, value in os.environ.items() if key.upper() in _KEEP}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(empty),
            "HOME": str(home),
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        }
    )
    return env


def git(
    base: Path, *args: str, input: bytes | None = None, env: dict[str, str] | None = None
) -> str:
    assert GIT is not None
    completed = subprocess.run(  # noqa: S603
        [GIT, *args],
        cwd=base,
        env=fixture_env(base) if env is None else env,
        input=input,
        capture_output=True,
        check=True,
    )
    return completed.stdout.decode().strip()


def make_source(
    base: Path,
    *,
    files: dict[str, bytes] | None = None,
    commits: int = 1,
    branch: str = "main",
    submodule: bool = False,
) -> Path:
    """A bare source repository with ``commits`` commits on ``branch``."""
    work = base / "fixture-work"
    git(base, "init", "-q", "-b", branch, str(work))
    for index in range(commits):
        (work / f"commit-{index}.txt").write_text(f"commit {index}\n", encoding="utf-8")
        if index == 0:
            for name, data in (files or {}).items():
                path = work / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        git(work, "add", "-A")
        if index == 0 and submodule:
            first = git(base, "hash-object", "-t", "blob", "--stdin", input=b"x")
            gitmodules = (
                '[submodule "sub"]\n\tpath = sub\n\turl = https://github.com/octocat/Spoon-Knife\n'
            )
            (work / ".gitmodules").write_text(gitmodules, encoding="utf-8")
            git(work, "add", ".gitmodules")
            git(work, "update-index", "--add", "--cacheinfo", f"160000,{first},sub")
        git(work, *IDENTITY, "commit", "-q", "-m", f"commit {index}")
    source = base / "source.git"
    git(base, "clone", "-q", "--bare", str(work), str(source))
    return source


def make_malformed_source(base: Path, *, branch: str = "main") -> Path:
    """A bare repository whose only commit has an author without an email."""
    source = base / "malformed.git"
    git(base, "init", "-q", "--bare", str(source))
    tree = git(base, "--git-dir", str(source), "mktree", input=b"")
    body = (
        f"tree {tree}\n"
        "author Fixture 1700000000 +0000\n"
        "committer Fixture <fixture@example.invalid> 1700000000 +0000\n"
        "\n"
        "malformed\n"
    ).encode()
    commit = git(
        base,
        "--git-dir",
        str(source),
        "hash-object",
        "-t",
        "commit",
        "--literally",
        "-w",
        "--stdin",
        input=body,
    )
    git(base, "--git-dir", str(source), "update-ref", f"refs/heads/{branch}", commit)
    return source


def head_commit(source: Path, branch: str = "main") -> str:
    return git(source.parent, "--git-dir", str(source), "rev-parse", f"refs/heads/{branch}")


def marker_script(path: Path, marker: Path) -> None:
    """A hook-like script that only appends its name to a marker file."""
    path.write_text(f"#!/bin/sh\necho {path.name} >> '{marker.as_posix()}'\n", encoding="utf-8")
    path.chmod(0o755)


def marker_configuration(base: Path) -> tuple[Path, Path, Path]:
    """Git configuration that runs a marker for every hook, template, and helper.

    Returns the configuration file, the hooks directory, and the marker file.
    """
    marker = base / "markers.log"
    hooks = base / "marker-hooks"
    hooks.mkdir()
    for name in ("reference-transaction", "post-checkout", "post-merge", "fsmonitor", "helper"):
        marker_script(hooks / name, marker)
    template = base / "marker-template"
    (template / "hooks").mkdir(parents=True)
    marker_script(template / "hooks" / "reference-transaction", marker)
    (template / "template-artifact").write_text("x\n", encoding="utf-8")
    config = base / "marker.gitconfig"
    config.write_text(
        "[core]\n"
        f"\thooksPath = {hooks.as_posix()}\n"
        f"\tfsmonitor = {(hooks / 'fsmonitor').as_posix()}\n"
        "[init]\n"
        f"\ttemplateDir = {template.as_posix()}\n"
        "[credential]\n"
        f"\thelper = {(hooks / 'helper').as_posix()}\n"
        "[protocol]\n"
        "\tallow = always\n",
        encoding="utf-8",
    )
    return config, hooks, marker

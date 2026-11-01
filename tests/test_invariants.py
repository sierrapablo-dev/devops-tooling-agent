"""Structural invariants of ADR-0007 that tests enforce.

- No executable entry points (Section 1).
- Only the runner module starts processes (Section 9).
"""

import ast
import tomllib
from pathlib import Path

import devops_tooling_agent

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "devops_tooling_agent"
RUNNER = PACKAGE / "runner.py"

# Modules whose import starts processes or reaches process creation APIs.
FORBIDDEN_MODULES = {
    "subprocess",
    "pty",
    "multiprocessing",
    "asyncio.subprocess",
    "_winapi",
    "_posixsubprocess",
    "ctypes",
    "webbrowser",
    "concurrent.futures.process",
}
# Names that start processes, wherever they are reached from.
FORBIDDEN_NAMES = {
    "system",
    "popen",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "posix_spawn",
    "posix_spawnp",
    "fork",
    "forkpty",
    "startfile",
    "create_subprocess_exec",
    "create_subprocess_shell",
    "ProcessPoolExecutor",
    "Popen",
    "__import__",
    "import_module",
}
FORBIDDEN_STRINGS = FORBIDDEN_NAMES | FORBIDDEN_MODULES


def test_version_matches_project_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert devops_tooling_agent.__version__ == project["version"]


def test_no_executable_entry_points() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for key in ("scripts", "gui-scripts", "entry-points"):
        assert key not in project
    assert "scripts" not in project.get("dynamic", [])
    assert "entry-points" not in project.get("dynamic", [])


def violations(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MODULES or alias.name == "importlib":
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in FORBIDDEN_MODULES or module.startswith("importlib"):
                found.append(f"from {module}")
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES or f"{module}.{alias.name}" in FORBIDDEN_MODULES:
                    found.append(f"from {module} import {alias.name}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(f".{node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(node.id)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in FORBIDDEN_STRINGS
        ):
            # getattr(os, "system") and similar dynamic lookups.
            found.append(f"string {node.value!r}")
    return found


def test_only_the_runner_starts_processes() -> None:
    sources = sorted(PACKAGE.rglob("*.py"))
    assert RUNNER in sources
    offenders = {
        str(path.relative_to(ROOT)): violations(path.read_text(encoding="utf-8"))
        for path in sources
        if path != RUNNER
    }
    assert {path: found for path, found in offenders.items() if found} == {}


def test_the_scan_detects_process_creation() -> None:
    samples = [
        "import subprocess",
        "import subprocess as sp",
        "from subprocess import run",
        "import os\nos.system('x')",
        "from os import system",
        "import os\nos.execvp('x', [])",
        "import os\nos.spawnv(0, 'x', [])",
        "import os\nos.posix_spawn('x', [], {})",
        "import os\nos.popen('x')",
        "import os\nos.fork()",
        "import os\nos.startfile('x')",
        "import asyncio\nasyncio.create_subprocess_exec('x')",
        "from asyncio import subprocess",
        "import pty",
        "import multiprocessing",
        "from concurrent.futures import ProcessPoolExecutor",
        "import ctypes",
        "import _winapi",
        "import importlib\nimportlib.import_module('subprocess')",
        "__import__('subprocess')",
        "import os\ngetattr(os, 'system')('x')",
    ]
    for sample in samples:
        assert violations(sample), sample


def test_runner_is_the_only_subprocess_importer() -> None:
    assert violations(RUNNER.read_text(encoding="utf-8"))

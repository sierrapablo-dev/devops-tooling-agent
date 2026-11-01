"""Fixed Git argument vectors (security policy Section 5.7.3, rules 8 and 9).

Every Git argument vector of the package is built by ``build_argv``, which
permits only the commands of rule 8 and always passes the options of rule 9.
Captured output may be inspected to classify a result, but it is never reported.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from devops_tooling_agent.constants import FETCH_DEPTH, GH_MIN_VERSION, GIT_MIN_VERSION
from devops_tooling_agent.environment import resolve_executable
from devops_tooling_agent.errors import CommandError, ErrorCode
from devops_tooling_agent.identifier import HOST, Repository
from devops_tooling_agent.resolve import valid_branch_name

STAGE_VERSION: Final = "git_version"
STAGE_ISOLATION: Final = "isolation_check"
STAGE_INIT: Final = "git_init"
STAGE_FETCH: Final = "git_fetch"
STAGE_REV_PARSE: Final = "git_rev_parse"

# Rule 9: options passed to every Git command, in this order. The gh helper of
# the fetch is inserted right after the helper reset.
HARDENING: Final[tuple[tuple[str, str], ...]] = (
    ("credential.helper", ""),
    ("core.askPass", ""),
    ("protocol.allow", "never"),
    ("protocol.https.allow", "always"),
    ("transfer.fsckObjects", "true"),
    ("gc.auto", "0"),
    ("maintenance.auto", "false"),
)
# Tests with local bare repositories only (ADR-0007 Section 9). The command-line
# interface never enables it.
FILE_PROTOCOL: Final = ("protocol.file.allow", "always")

FETCH_OPTIONS: Final = (
    "--no-tags",
    "--no-recurse-submodules",
    "--no-auto-maintenance",
    "--no-write-fetch-head",
    f"--depth={FETCH_DEPTH}",
    "--quiet",
)
READ_COMMANDS: Final = frozenset({"rev-parse", "ls-tree", "cat-file", "log"})

# Verified with a malformed fixture (docs/claude-code-environment.md, 8.1.5).
FSCK_FRAGMENT: Final = b"fsck error"

_VERSION_LINE: Final = re.compile(
    rb"\Agit version ([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})(?=[ .(\r\n-]|\Z)"
)
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")
_OBJECT_ID: Final = re.compile(rb"\A([0-9a-f]{40}|[0-9a-f]{64})\n?\Z")

Version = tuple[int, int, int]


@dataclass(frozen=True)
class GitOptions:
    """Internal options. ``allow_file_protocol`` is set only by tests."""

    allow_file_protocol: bool = False


def canonical_url(repository: Repository) -> str:
    """The fetch URL, built only from the validated canonical identity."""
    return f"https://{HOST}/{repository.full_name}"


def config_entries(options: GitOptions, *, helper: str | None = None) -> list[tuple[str, str]]:
    entries = list(HARDENING)
    if helper is not None:
        entries.insert(1, ("credential.helper", helper))
    if options.allow_file_protocol:
        entries.append(FILE_PROTOCOL)
    return entries


def expected_scope_lines(options: GitOptions) -> list[str]:
    """What ``config --list --show-scope`` must print under isolation, exactly."""
    return [f"command\t{key.lower()}={value}" for key, value in config_entries(options)]


def _valid_url(url: str, options: GitOptions) -> bool:
    if url.startswith(f"https://{HOST}/"):
        return True
    return options.allow_file_protocol and url.startswith("file://")


def build_argv(
    git: Path,
    command: str,
    *,
    options: GitOptions,
    git_dir: Path | None = None,
    url: str | None = None,
    branch: str | None = None,
    helper: str | None = None,
    read_args: Sequence[str] = (),
) -> list[str]:
    """Build the argument vector of one permitted Git command.

    ``command`` is ``version``, ``init`` (``git_dir`` is the repository to
    create), ``config``, ``get-url``, ``fetch``, or a read command. Raises
    ``ValueError`` for anything that rule 8 does not permit, which is a defect
    of the caller.
    """
    if not git.is_absolute():
        raise ValueError("git must be an absolute path")
    if helper is not None and command != "fetch":
        raise ValueError("only the fetch receives the credential helper")
    if (command == "version") != (git_dir is None):
        raise ValueError("the repository location is required exactly when it is used")
    if git_dir is not None and not git_dir.is_absolute():
        raise ValueError("the repository location must be absolute")
    if url is not None and command not in {"get-url", "fetch"}:
        raise ValueError("only get-url and fetch take a URL")
    if branch is not None and command != "fetch":
        raise ValueError("only fetch takes a branch")
    if read_args and command not in READ_COMMANDS:
        raise ValueError("only read commands take arguments")

    argv = [str(git)]
    for key, value in config_entries(options, helper=helper):
        argv += ["-c", f"{key}={value}"]
    if command == "version":
        return [*argv, "version"]
    if git_dir is None:
        raise ValueError("the repository location is required")
    if command == "init":
        # The only command that creates a repository: bare, with an empty template.
        return [*argv, "init", "--bare", "--template=", "--quiet", str(git_dir)]
    argv.append(f"--git-dir={git_dir}")
    if command == "config":
        return [*argv, "config", "--list", "--show-scope"]
    if command == "get-url":
        if url is None or not _valid_url(url, options):
            raise ValueError("invalid URL")
        return [*argv, "ls-remote", "--get-url", url]
    if command == "fetch":
        if url is None or not _valid_url(url, options):
            raise ValueError("invalid URL")
        if branch is None or not valid_branch_name(branch):
            raise ValueError("invalid branch")
        if helper is None and not options.allow_file_protocol:
            raise ValueError("the fetch requires the gh credential helper")
        refspec = f"+refs/heads/{branch}:refs/heads/{branch}"
        return [*argv, "fetch", *FETCH_OPTIONS, url, refspec]
    if command in READ_COMMANDS:
        if any(not arg or "\x00" in arg for arg in read_args):
            raise ValueError("invalid read argument")
        extra = ["--no-show-signature"] if command == "log" else []
        return [*argv, command, *extra, *read_args]
    raise ValueError("command not permitted")


def rev_parse_args(branch: str) -> list[str]:
    if not valid_branch_name(branch):
        raise ValueError("invalid branch")
    return ["--verify", "--quiet", "--end-of-options", f"refs/heads/{branch}^{{commit}}"]


def parse_object_id(stdout: bytes) -> str | None:
    match = _OBJECT_ID.match(stdout)
    return match.group(1).decode("ascii") if match else None


def gh_helper(gh: Path) -> str:
    """The ``gh`` credential helper value, with single-quote quoting.

    Git runs a helper that starts with ``!`` through its own shell. A path that
    contains a single quote or a control character is refused, never escaped.
    """
    text = gh.as_posix()
    if "'" in text or _CONTROL.search(text):
        raise CommandError(
            ErrorCode.ENVIRONMENT_ERROR,
            {"reason": "unsupported_path", "tool": "gh", "minimum_version": GH_MIN_VERSION},
        )
    return f"!'{text}' auth git-credential"


def environment_error(reason: str) -> CommandError:
    return CommandError(
        ErrorCode.ENVIRONMENT_ERROR,
        {"reason": reason, "tool": "git", "minimum_version": GIT_MIN_VERSION},
    )


def resolve_git(env: Mapping[str, str], *, cwd: str, windows: bool) -> Path:
    """Resolve ``git`` the way ``gh`` is resolved. Raises ``ENVIRONMENT_ERROR``."""
    executable = resolve_executable("git", env, cwd=cwd, windows=windows)
    if executable is None:
        raise environment_error("not_found")
    return executable


def parse_version(stdout: bytes) -> Version | None:
    """Parse the first line of ``git version``. Returns ``None`` if unrecognized."""
    match = _VERSION_LINE.match(stdout)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def minimum_version() -> Version:
    major, minor, patch = (int(part) for part in GIT_MIN_VERSION.split("."))
    return major, minor, patch

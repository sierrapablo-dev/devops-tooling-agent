"""Static invariants of the committed Claude Code project settings.

These tests check the configuration file only. They do not prove runtime
behavior, which is verified against the supported Claude Code version and
recorded in docs/claude-code-environment.md.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / ".claude" / "settings.json"
SHELLS = ("Bash", "PowerShell")

ALLOWED_TOP_LEVEL_KEYS = {
    "$schema",
    "permissions",
    "enableAllProjectMcpServers",
    "disableSkillShellExecution",
    "crossSessionInbound",
}
ALLOWED_PERMISSION_KEYS = {
    "disableBypassPermissionsMode",
    "blockReadsOutsideWorkingDirectories",
    "deny",
    "ask",
    "allow",
}

# Security policy section 9.4: the only commands pre-approved for every shell tool.
EXPECTED_ALLOW_COMMANDS = {
    "git status *",
    "git log *",
    "git diff *",
    "git show *",
    "git blame *",
    "git branch --list *",
    "git remote -v",
    "git fetch *",
    "git ls-files *",
    "git rev-parse *",
    "gh issue view *",
    "gh issue list *",
    "gh pr view *",
    "gh pr list *",
    "gh pr diff *",
    "gh pr checks *",
    "gh run view *",
    "gh run list *",
}

# Shell-specific rules that intentionally have no counterpart in the other shell.
SHELL_SPECIFIC_DENY = {
    "Bash(set)",
    "Bash(export)",
    "Bash(export -p)",
    "Bash(declare -p*)",
    "Bash(declare -x*)",
    "PowerShell(Get-ChildItem env:)",
    "PowerShell(Get-ChildItem env:**)",
    "PowerShell(Get-Item env:**)",
}

REQUIRED_DENY_COMMANDS = {
    "gh pr merge *",
    "gh pr review *--approve*",
    "gh api *pulls/*/merge*",
    "gh api *mergePullRequest*",
    "gh repo edit *",
    "gh repo delete *",
    "gh secret *",
    "gh variable *",
    "gh auth token *",
    "gh auth refresh *",
    "git push --force *",
    "git push -f *",
    "git push --mirror *",
    "git push *--no-verify*",
    "git commit *--no-verify*",
    "git config --global *",
    "git config --system *",
    "git push * main",
    "git push *:main",
    "git push *refs/heads/main",
    "git push * master",
    "git -C * push * main",
    "git -c * push * main",
    "printenv *",
    "env",
    "git credential *",
}

REQUIRED_DENY_MCP = {
    "mcp__*__merge_pull_request",
    "mcp__*__delete_repository",
    "mcp__*__create_repository",
    "mcp__*__pull_request_review_write",
    "mcp__*__push_files",
    "mcp__*__create_or_update_file",
    "mcp__*__delete_file",
}

# Security policy section 8.3, translated to path patterns that also cover attached targets.
REQUIRED_CREDENTIAL_PATHS = {
    "~/.ssh/**",
    "~/.gnupg/**",
    "~/.aws/**",
    "~/.azure/**",
    "~/.config/gcloud/**",
    "~/.kube/**",
    "~/.oci/**",
    "~/.config/gh/hosts.yml",
    "~/.git-credentials",
    "~/.netrc",
    "~/.npmrc",
    "~/.pypirc",
    "~/.docker/config.json",
    "~/.terraform.d/credentials.tfrc.json",
    "~/.claude/.credentials.json",
    "//**/.env",
    "//**/.env.*",
    "//**/*.pem",
    "//**/*.key",
    "//**/*.p12",
    "//**/*.pfx",
    "//**/id_rsa*",
    "//**/id_ed25519*",
    "//**/*.tfstate",
    "//**/*.tfstate.backup",
    "//**/.terraform/**",
}

REQUIRED_ASK_COMMANDS = {
    "git push *",
    "git reset *",
    "git clean *",
    "git restore *",
    "git commit *",
    "git switch *",
    "git checkout *",
    "git rebase *",
    "git config *",
    "git -C *",
    "git -c *",
    "git fetch *--upload-pack*",
    "git log *--output*",
    "git diff *--output*",
    "git show *--output*",
    "gh api *",
    "gh pr create *",
    "bash *",
    "pwsh *",
    "cmd *",
    "python *",
    "uv *",
    "npm *",
    "terraform *",
    "kubectl *",
    "aws *",
    "rm *",
    "Remove-Item *",
}

REQUIRED_ASK_OTHER = {
    "mcp__*",
    "Edit(/.claude/**)",
    "Edit(/CLAUDE.md)",
    "Edit(/.github/workflows/**)",
    "Read(~/.claude/settings.json)",
    "Read(~/.claude/settings.local.json)",
}

CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-ant-[A-Za-z0-9-]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

RULE_PATTERN = re.compile(r"^(?P<tool>[A-Za-z_*][\w*-]*)(?:\((?P<spec>.*)\))?$")


@pytest.fixture(scope="module")
def settings() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    return data


def _rules(settings: dict[str, Any], kind: str) -> list[str]:
    rules: list[str] = settings["permissions"][kind]
    return rules


def _shell_rules(command: str) -> set[str]:
    return {f"{shell}({command})" for shell in SHELLS}


def test_settings_use_only_reviewed_keys(settings: dict[str, Any]) -> None:
    assert set(settings) <= ALLOWED_TOP_LEVEL_KEYS
    assert set(settings["permissions"]) <= ALLOWED_PERMISSION_KEYS


def test_settings_contain_no_environment_paths_or_mode(settings: dict[str, Any]) -> None:
    assert "env" not in settings
    assert "additionalDirectories" not in settings["permissions"]
    assert "defaultMode" not in settings["permissions"]


def test_security_switches_are_set(settings: dict[str, Any]) -> None:
    assert settings["permissions"]["disableBypassPermissionsMode"] == "disable"
    assert settings["permissions"]["blockReadsOutsideWorkingDirectories"] is True
    assert settings["disableSkillShellExecution"] is True
    assert settings["crossSessionInbound"] in {"hold", "refuse"}
    assert settings.get("enableAllProjectMcpServers") is not True


@pytest.mark.parametrize("kind", ["deny", "ask", "allow"])
def test_rules_are_well_formed_and_unique(settings: dict[str, Any], kind: str) -> None:
    rules = _rules(settings, kind)
    assert len(rules) == len(set(rules))
    for rule in rules:
        assert RULE_PATTERN.match(rule), rule
        # Write(path) rules never match; Edit(path) covers every file-writing tool.
        assert not rule.startswith("Write("), rule


def test_no_rule_appears_in_more_than_one_list(settings: dict[str, Any]) -> None:
    deny, ask, allow = (set(_rules(settings, kind)) for kind in ("deny", "ask", "allow"))
    assert not deny & ask
    assert not deny & allow
    assert not ask & allow


def test_allow_list_matches_security_policy(settings: dict[str, Any]) -> None:
    expected = set().union(*(_shell_rules(command) for command in EXPECTED_ALLOW_COMMANDS))
    assert set(_rules(settings, "allow")) == expected


@pytest.mark.parametrize("kind", ["deny", "ask", "allow"])
def test_shell_rules_are_defined_for_every_shell(settings: dict[str, Any], kind: str) -> None:
    rules = set(_rules(settings, kind))
    for rule in rules - SHELL_SPECIFIC_DENY:
        match = RULE_PATTERN.match(rule)
        assert match
        if match["tool"] in SHELLS:
            assert _shell_rules(match["spec"]) <= rules, rule


def test_required_deny_rules_are_present(settings: dict[str, Any]) -> None:
    deny = set(_rules(settings, "deny"))
    for command in REQUIRED_DENY_COMMANDS:
        assert _shell_rules(command) <= deny, command
    assert REQUIRED_DENY_MCP.issubset(deny)
    for path in REQUIRED_CREDENTIAL_PATHS:
        assert {f"Read({path})", f"Edit({path})"} <= deny, path


def test_required_ask_rules_are_present(settings: dict[str, Any]) -> None:
    ask = set(_rules(settings, "ask"))
    for command in REQUIRED_ASK_COMMANDS:
        assert _shell_rules(command) <= ask, command
    assert REQUIRED_ASK_OTHER.issubset(ask)


def test_settings_contain_no_credentials_or_machine_paths() -> None:
    text = SETTINGS_PATH.read_text(encoding="utf-8")
    for pattern in CREDENTIAL_VALUE_PATTERNS:
        assert not pattern.search(text), pattern.pattern
    paths = text.replace("https://", "")
    assert not re.search(r"\b[A-Za-z]:[\\/]", paths)
    assert not re.search(r"//(?!\*\*/)[^\s\")]", paths)
    assert "/Users/" not in paths
    assert "/home/" not in paths


def test_personal_files_are_ignored() -> None:
    entries = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".claude/settings.local.json" in entries
    assert "CLAUDE.local.md" in entries


def test_no_separate_commands_layer() -> None:
    assert not (ROOT / ".claude" / "commands").exists()

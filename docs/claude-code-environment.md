# Claude Code Environment

This document describes the Claude Code project environment of DevOps Tooling Agent: the `.claude/` structure, the committed settings, the conventions for skills and subagents, and the verified behavior and limits of the configuration.

[`docs/architecture.md`](architecture.md) is the authoritative architecture description, and [`docs/security-policy.md`](security-policy.md) is the normative security policy. This document records how the Claude Code configuration implements [security policy Section 9](security-policy.md#9-required-claude-code-controls) and what was verified. It does not define new security rules.

## 1. Structure

```text
.claude/
└── settings.json      # Committed project settings: permission rules and security settings
```

The following are intentionally absent:

| Path | Reason |
|---|---|
| `.claude/commands/` | No separate commands layer exists ([ADR-0001](adr/0001-claude-code-as-orchestration-engine.md)). Skills are invoked as slash commands. |
| `.claude/agents/` | No subagent is justified yet ([architecture Section 6.2](architecture.md#62-subagents)). |
| `.claude/skills/` | Created together with the first skill. |
| `.claude/hooks/` | Security hooks are implemented in the Python package ([HG-1](security-policy.md#10-security-hook-requirements)) and only registered in `.claude/settings.json`. |
| `.claude/rules/`, `CLAUDE.local.md` | `CLAUDE.md` holds the project instructions. Personal instruction files are not versioned. |
| `.mcp.json`, plugin and provider settings | No project MCP server or provider configuration exists. Provider authentication stays in native, operator-level mechanisms ([configuration Section 4](configuration.md#4-provider-configuration)). |

## 2. Conventions

### 2.1 Skills

- Each skill lives in `.claude/skills/<skill-name>/SKILL.md`, uses a kebab-case name, and represents one cohesive capability.
- Skills are invoked directly as slash commands. Logic is not duplicated in `.claude/commands/`.
- Skills that perform mutating operations make their authorization points explicit.
- Skills do not use inline shell execution (`` !`command` ``). It is disabled by `disableSkillShellExecution`.
- Skills do not use `allowed-tools` to pre-approve operations outside tier R. Claude Code applies `allowed-tools` without workspace trust and pre-approves those tools for the turn that invokes the skill. Deny and ask rules still apply.

### 2.2 Subagents

A subagent is added in `.claude/agents/<name>.md` only when a task meets the criteria in [architecture Section 6.2](architecture.md#62-subagents). The pull request that adds it states the justification.

### 2.3 Hooks

Security hooks are registered in the `hooks` key of `.claude/settings.json` and implemented in the Python package. A hook that inspects shell commands matches `Bash|PowerShell`, and hooks that guard external operations also match MCP tools (`mcp__.*`), the `Skill` tool, and the `Agent` tool. No hook is configured yet. The requirements are in [security policy Section 10](security-policy.md#10-security-hook-requirements).

## 3. Versioning and settings scopes

| File | Scope | Versioned |
|---|---|---|
| `.claude/settings.json` | Every session started from this repository | Yes. Changes are reviewed in pull requests. |
| `.claude/settings.local.json` | The operator, in this repository only | No. Excluded in `.gitignore`. |
| `CLAUDE.local.md` | The operator, in this repository only | No. Excluded in `.gitignore`. |
| `~/.claude/settings.json` | The operator, in every project | No. Operator preferences such as `defaultMode`, model, theme, and plugins. |

Committed settings contain no credentials, no `env` block, no `additionalDirectories`, no `defaultMode`, and no machine-specific paths. The configuration invariants are checked by `tests/test_claude_settings.py`. Those tests check the file only; runtime behavior is covered by [Section 8](#8-verified-behavior).

## 4. Settings

| Setting | Value | Purpose |
|---|---|---|
| `permissions.deny` | Rules for tier P | Prohibited operations ([policy Section 9.2](security-policy.md#92-deny-rules)), protected-branch pushes, direct remote mutations through MCP, and credential locations |
| `permissions.ask` | Rules for tiers W2, S, and X | Operations that need a permission prompt ([policy Section 9.3](security-policy.md#93-ask-rules)), including `mcp__*` for every MCP tool |
| `permissions.allow` | Policy Section 9.4 only | Read-only `git` and `gh` commands for `Bash` and `PowerShell` |
| `permissions.disableBypassPermissionsMode` | `"disable"` | Prevents sessions from running in `bypassPermissions` mode (C-4). See [Section 5.2](#52-disablebypasspermissionsmode) |
| `permissions.blockReadsOutsideWorkingDirectories` | `true` | File tools refuse reads outside the agent environment and attached targets |
| `disableSkillShellExecution` | `true` | Replaces inline shell in skills and commands from every non-managed source, including attached targets |
| `crossSessionInbound` | `"hold"` | Messages from other local Claude Code sessions are shown as a notice instead of being delivered |
| `enableAllProjectMcpServers` | `false` | Project MCP servers are never approved automatically (C-6) |

The number of rules is not a measure of coverage. Prefix-based rules match the command text that Claude writes, and [Section 9](#9-known-bypasses-and-gaps) lists the verified paths around them.

Rule translation notes:

- `Write(path)` rules never match in Claude Code. File-writing tools are governed by `Edit(path)` rules, and a `Read(path)` deny rule also blocks writing the same path.
- Patterns such as `.env` without an anchor match only under the current directory. Credential patterns that must also cover attached targets use `//**/` (any absolute path on any drive). Windows equivalents of [Section 8.3](security-policy.md#83-credential-locations) are listed under `~/AppData/Roaming/`.
- Deny rules take precedence over ask rules, so `.env.example`, which the policy classifies as S, is effectively denied by the `.env.*` rule.
- Protected-branch push rules cover `main` and `master`, including the `git -C <path>` and `git -c <key>=<value>` forms. Other protected branch names of a target rely on hook H-3 and the target's own platform controls.

## 5. Interaction with other settings and flags

### 5.1 Precedence

- Deny rules are evaluated first, then ask rules, then allow rules, regardless of the file that defines them. An `allow` rule in user, local, or `--settings` scope cannot override a project `deny` or `ask` rule (verified).
- `allow` rules of `.claude/settings.json` apply only after the operator accepts the workspace trust dialog. In `claude -p` sessions they are never applied. `deny` and `ask` rules apply without trust (verified).
- Scalar settings of `.claude/settings.local.json` and `--settings` take precedence over `.claude/settings.json`. They can set `disableSkillShellExecution` to `false` or `disableAllHooks` to `true`. `crossSessionInbound` from project settings applies only when it is stricter than the user value, and a `true` for `blockReadsOutsideWorkingDirectories` in any file applies.

### 5.2 `disableBypassPermissionsMode`

With the setting present, starting a session with `--dangerously-skip-permissions` or `--permission-mode bypassPermissions` does not produce an error. Claude Code ignores the request and the session starts in the mode configured in user settings, which on the operator's machine is `auto` (verified). The same flag in a directory without the setting starts in `bypassPermissions` mode.

This silent fallback is not a rejection. Nothing reports that the bypass request was refused, so tooling or operators cannot rely on a failed start as evidence. A `--settings` value that tries to clear the key is rejected as invalid, and the whole `--settings` source is then not loaded.

### 5.3 Operator settings

- The operator's `defaultMode: "auto"` applies to sessions in this repository. Project settings cannot set `auto` and do not set any mode. Every operation that no rule matches is decided by the auto mode classifier, which is not a security control ([policy Section 3](security-policy.md#3-enforcement-layers)).
- `skipDangerousModePermissionPrompt` has no effect in this repository, because bypass mode is not entered.
- An `env` block in user settings is passed to every command the session runs, including target-defined code. Credentials MUST NOT be placed there ([policy Section 8.1](security-policy.md#81-storage-and-supply)).

### 5.4 Project settings are not tamper-proof

Project settings are defense in depth (L2). They do not hold when:

- Claude Code is started from another directory, or with `--setting-sources` that excludes `project`.
- `.claude/settings.local.json` or `--settings` overrides a scalar setting.
- An installed mod approves a call. Without managed settings or a Team or Enterprise login, a mod can approve calls that deny rules refuse.
- The file is edited. Edits to `.claude/**` are ask rules, but an approved edit takes effect immediately.

Only managed settings resist these overrides, and they are not available for the operator's account type. Platform controls ([policy Section 11](security-policy.md#11-platform-controls)) remain the authoritative boundary.

## 6. Working with target repositories

1. Start Claude Code from the root of this repository and attach each target for the session: `claude --add-dir <target-path>` or `/add-dir <target-path>`. Never add target paths to committed settings. Attaching a directory does not select it as a target; selection follows [configuration Section 3.2](configuration.md#32-target-selection).
2. Before attaching a target, list its `.claude/skills/`, `.claude/commands/`, and `.claude/agents/` directories and report any components found. They load into the session and MUST NOT be invoked. Until hook H-4 exists, add a `Skill(<name>)` or `Agent(<name>)` deny rule for each one to `.claude/settings.local.json` for the session.
3. Do not set `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD`. Without it, no instruction file of an attached target is loaded (verified).
4. Git commands in a target need approval: `cd <target> && git …` prompts by Claude Code's built-in behavior, and `git -C <target> …` matches an ask rule.

## 7. Operational friction

This configuration is more restrictive than the W1 authorization model of the [security policy](security-policy.md#4-operation-tiers). An approved task plan does not suppress individual permission prompts. Creating branches, committing, pushing the task branch, running `uv` quality checks, and Git commands in targets prompt each time. Rules are not relaxed to reduce prompts. A rule moves to `allow` only through a reviewed change that the policy permits, such as the conditional move of quality checks after hook H-5.

## 8. Verified behavior

Verified on 2026-10-30 with Claude Code 2.1.314 on Windows 11 with the PowerShell tool enabled.

Method:

- Headless sessions (`claude -p --permission-prompts none --output-format stream-json`) were started from a clone of this repository with the candidate `.claude/settings.json`, or from an empty directory with rule subsets passed through `--settings`, which isolates deny, ask, and allow matching.
- The target was a fixture created by the verifier: a Git repository without hooks, with dummy credential files, instruction files, a skill with inline shell, and a local bare repository as its remote. No third-party code was executed.
- Probes were harmless if executed: pushes went to the local bare repository, `gh` used an invalid token against a nonexistent repository, and sessions whose probes could print the environment ran with a minimal process environment and without user settings.
- Results come from the harness tool results and the session's reported permission mode, not from the model's own report.

| Area | Result |
|---|---|
| Shell tools | `Bash` and `PowerShell` are both available. Claude Code documents that a scoped `Bash` deny rule turns PowerShell off unless a `PowerShell` rule exists, which is the likely reason the tool was unavailable in the earlier verification (RR-7). Every rule is defined for both tools. |
| Deny | Matched in both shells, through compound commands (`cd … &&`, `;`), leading `-C` and `-c` options, and the PowerShell call operator `&`. |
| Ask | Matched for every listed form, including `git -C`, `git -c`, `--upload-pack`, `--output`, shell wrappers, `.exe` variants, `env` and `xargs` wrappers, toolchains, and the PowerShell aliases `rm`, `del`, `ri`, and `iex`. |
| Allow | `git status`, `git log`, `git fetch`, and `gh pr view` run without a prompt. `cd <target> && git …` and `git -C <target> …` are not auto-approved. |
| Precedence | Deny beats an explicit allow, and ask beats an explicit allow, for shell, file, and MCP connector rules. |
| Protected branches | Pushes to `main` and `master` are denied for plain, `HEAD:main`, `<branch>:main`, `refs/heads/main`, `-u`, `--delete`, `:main`, force, `+refspec`, `--all`, `git -C`, and `git -c` forms in both shells. |
| Working branches | No false positives: `git switch -c`, commits whose messages mention `push`, `main`, `-n`, or `+1`, `git push -u`, `--force-with-lease` and explicit refspecs to the task branch, `git stash push -m`, `rebase`, `fetch`, and `gh pr create` or `comment` are not denied. |
| GitHub operations | `gh pr merge`, `gh pr review --approve`, the REST merge and `/merges` endpoints, GraphQL `mergePullRequest`, ruleset endpoints, `gh auth token`, and `gh auth status --show-token` are denied. |
| MCP | `mcp__*` ask applies to claude.ai connector tools. |
| Environment dumps | `printenv`, `env`, `set`, `export -p`, `Get-ChildItem env:` and its aliases, and `Get-Item env:<name>` are denied. |
| Credential files | See [Section 9.1](#91-credential-access-through-shell-commands). |
| Bypass mode | Silent fallback described in [Section 5.2](#52-disablebypasspermissionsmode). |
| Reads outside working directories | `Read`, `Grep`, and `Glob` are refused. `cat` and `Get-Content` require approval. |
| Instruction files | With `--add-dir`, the target's `CLAUDE.md`, `.claude/rules/`, and nested `CLAUDE.md` were not loaded, including after reading a file in the nested directory. They loaded only with `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`. Evidence: `InstructionsLoaded` hook log. |
| Target skills | Loaded and invocable from `--add-dir`. Inline shell is replaced by `[shell command execution disabled by policy]`. |
| Settings validity | `claude doctor` reports no rejected or ambiguous rules. |

## 9. Known bypasses and gaps

### 9.1 Credential access through shell commands

`Read` and `Edit` deny rules do not prevent credential access through the shell. They cover Claude's file tools and the file commands Claude Code recognizes.

| Access path | Blocked by deny rules |
|---|---|
| `Read`, `Grep`, `Glob`, `Write`, `Edit` | Yes |
| `cat`, `head`, `tail`, `sed`, `awk`, `base64`, `cp`, output redirection | Yes |
| PowerShell `Get-Content` and aliases, `Select-String`, `Copy-Item` | Yes |
| `git show <rev>:<credential path>` | Yes, through dedicated deny rules |
| `grep -r <directory>`, `git grep` | No (`git grep` requires approval) |
| Interpreters (`python -c`, `node -e`) | No (interpreters require approval) |
| Input redirection inside a loop (`while read …; done < file`) | No |
| .NET file APIs in PowerShell (`[System.IO.File]::ReadAllText`) | No |
| Environment variable expansion (`echo $NAME`, `$env:NAME`) | No |
| `git log -p` and `git diff` of committed credential files | No rule matches them (not probed) |

Future controls: hook H-6 escalates or blocks known patterns, such as recursive searches over target roots, Git object reads of credential paths, and .NET file APIs. A complete control requires operating-system isolation, such as a validated sandbox (not available on native Windows; requires its own decision record, see [policy Section 12](security-policy.md#12-sandbox)) or a dedicated user or container without credentials. Platform controls limit the impact: short-lived least-privilege tokens and secret scanning.

### 9.2 Pushes and remote mutations

- `git push` without a refspec, or `git push origin HEAD`, while the default branch is checked out cannot be matched by a rule. Hook H-3 covers it.
- Quoted subcommands (`git 'push'`) and absolute program paths are not reliably matched.
- Shell wrappers (`bash -c`, `pwsh -Command`) bypass deny rules. They match ask rules.
- `git commit` with `-n` after other options is not denied. It matches the `git commit` ask rule.
- REST and GraphQL operations that update refs or file contents, such as `PATCH …/git/refs/heads/main` or `PUT …/contents/…` with `branch=main`, cannot be distinguished from reads by a native pattern and remain ask-only under `gh api *`.
- MCP rules cannot match tool parameters. MCP tools that write files or refs directly (`push_files`, `create_or_update_file`, `delete_file`) are denied, and every other MCP tool is ask-only.

GitHub is the independent L3 enforcement layer for these paths. The `protect-main` ruleset of this repository targets the default branch and `refs/heads/main`, requires pull requests with squash merges and required status checks, blocks deletion and non-fast-forward updates, and has no bypass actors (`current_user_can_bypass: never`, read through the API on 2026-10-30). It was not tested by attempting a prohibited push. The operator's administrator identity can still change the ruleset itself (RR-1). The ruleset endpoints are denied in L2.

### 9.3 Other gaps

- `allow` rules for `git fetch`, `git log`, `git show`, and `git diff` would execute programs (`--upload-pack`) or write files (`--output`) if those flags were not ask rules (verified with the allow rules alone). Other option-based execution paths of allowed commands are not known to be covered.
- Target skills, slash commands, and subagents load from attached directories and their `allowed-tools` apply without trust (RR-4).
- Commands not covered by any rule are decided by the auto mode classifier.

## 10. Unverified behavior

The following are not established and MUST NOT be relied on:

| Behavior | Status |
|---|---|
| MCP behavior in interactive sessions | Partially observed: when the settings loaded into an interactive session, the seven denied GitHub MCP tools were removed from its context. Prompts for `mcp__*` are unverified. The GitHub plugin's MCP server did not connect in `claude -p` sessions. |
| Ask prompts and allow rules in interactive sessions under `auto` mode | Unverified. Documented behavior is that explicit ask rules always prompt; headless sessions deny instead of prompting. |
| `crossSessionInbound: "hold"` at runtime | Unverified. The value is accepted by `claude doctor`. |
| Impact of `blockReadsOutsideWorkingDirectories` on auto memory and the session scratchpad | Unverified. Documentation states that files Claude Code needs remain readable; the session scratchpad is outside the working directories. |
| Loading of a target's `AGENTS.md` | Unverified. The `InstructionsLoaded` hook does not observe `AGENTS.md`. |
| Absolute program paths (`/usr/bin/git … push`) | A probe was blocked, but the behavior is undocumented and not relied on. |

## 11. Re-verification

Re-verify [Section 8](#8-verified-behavior) to [Section 10](#10-unverified-behavior) when the supported Claude Code version changes, when `.claude/settings.json` changes, or when a new shell or MCP server is introduced. Use only fixtures created for the verification, keep probes harmless if executed, and run probes that could expose the environment without user settings and with a minimal process environment.

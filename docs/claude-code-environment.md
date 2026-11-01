# Claude Code Environment

This document describes the Claude Code project environment of DevOps Tooling Agent: the `.claude/` structure, the committed settings, the conventions for skills and subagents, and the verified behavior and limits of the configuration.

[`docs/architecture.md`](architecture.md) is the authoritative architecture description, and [`docs/security-policy.md`](security-policy.md) is the normative security policy. This document records how the Claude Code configuration implements [security policy Section 9](security-policy.md#9-required-claude-code-controls) and what was verified. It does not define new security rules.

## 1. Structure

```text
.claude/
├── settings.json                 # Committed project settings: permission rules and security settings
└── skills/
    └── analyze-repository/       # Read-only, evidence-based analysis of a selected target repository
        ├── SKILL.md
        ├── inspection-guide.md
        └── report-template.md
```

The following are intentionally absent:

| Path | Reason |
|---|---|
| `.claude/commands/` | No separate commands layer exists ([ADR-0001](adr/0001-claude-code-as-orchestration-engine.md)). Skills are invoked as slash commands. |
| `.claude/agents/` | No subagent is justified yet ([architecture Section 6.2](architecture.md#62-subagents)). |
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
- The static conventions above are checked by `tests/test_claude_skills.py` for every skill.

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
2. Before attaching a target, list its `.claude/skills/`, `.claude/commands/`, and `.claude/agents/` directories and report any components found. They load into the session and MUST NOT be invoked. Until hook H-4 exists, add a `Skill(<name>)` or `Agent(<name>)` deny rule for each one to `.claude/settings.local.json` for the session. Use the name under which the session lists the component: a target skill whose name is already taken is listed as `<directory-name>:<skill-name>` ([Section 8](#8-verified-behavior)).
3. Do not set `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD`. Without it, no instruction file of an attached target is loaded (verified).
4. Git commands in a target need approval: `cd <target> && git …` prompts by Claude Code's built-in behavior, and `git -C <target> …` matches an ask rule.
5. Remote GitHub repositories are not attached. They are selected by identifier and acquired by the Python package ([configuration Section 3.2](configuration.md#32-target-selection), [ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md)). Package invocations prompt under the `uv` ask rule ([security policy Section 5.7.2](security-policy.md#572-invocation)).

## 7. Operational friction

This configuration is more restrictive than the W1 authorization model of the [security policy](security-policy.md#4-operation-tiers). An approved task plan does not suppress individual permission prompts. Creating branches, committing, pushing the task branch, running `uv` quality checks, and Git commands in targets prompt each time. Rules are not relaxed to reduce prompts. A rule moves to `allow` only through a reviewed change that the policy permits, such as the conditional move of quality checks after hook H-5.

## 8. Verified behavior

Verified on 2026-10-30 with Claude Code 2.1.314 on Windows 11 with the PowerShell tool enabled. Later patch versions accepted without full re-verification are listed in [Section 11](#11-re-verification).

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
| Symbolic links in attached directories | `Read`, and `Grep` with a single file as its path, follow a link inside an attached directory to a destination outside every working directory without a prompt. `Glob`, including literal patterns, does not return links, and directory-wide `Grep` searches skip linked files and directories. `Read` deny rules are evaluated against the link's destination: a deny rule for the destination blocked reads through a file link and a directory link. Verified with native Windows symbolic links to a harmless marker file. |
| Instruction files | With `--add-dir`, the target's `CLAUDE.md`, `.claude/rules/`, and nested `CLAUDE.md` were not loaded, including after reading a file in the nested directory. They loaded only with `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`. Evidence: `InstructionsLoaded` hook log. |
| Target skills | Loaded and invocable from `--add-dir`. Inline shell is replaced by `[shell command execution disabled by policy]`. |
| Skill name collisions | A target skill with the same name as a project skill does not replace it. The target skill is listed as `<directory-name>:<skill-name>`, for example `collision-target:analyze-repository`, and the unprefixed name remains the project skill. Two attached copies of the same skill were listed under both directory names. Target skills and commands with unique names, such as `deploy-helper`, are listed without a prefix. Evidence: the session's `skills` and `slash_commands` lists and the debug log (`project: 1, additional: 2`), with every `Skill` invocation denied, so no skill was invoked. Collisions with user-level or plugin skills were not tested. |
| Settings validity | `claude doctor` reports no rejected or ambiguous rules. |

### 8.1 Remote analysis prerequisites

Verified on 2026-10-31 for the collector-based remote analysis design (#14) with Claude Code 2.1.316, Git 2.49.0.windows.1 with git-lfs 3.6.1, gh 2.92.0, Python 3.13.2, and uv 0.11.19 on Windows 11 Pro 10.0.26300 with PowerShell 7.6.9, using the committed `.claude/settings.json`. No other platform was available. These results record behavior only. The decisions that rely on them are recorded in [ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md) and [ADR-0007](adr/0007-python-invocation-contract.md) ([Section 8.1.8](#818-findings-for-the-acquisition-design-15)).

Method:

- Probes ran in an interactive session started from this repository, and in headless sessions (`claude -p --permission-prompts none --output-format stream-json --verbose`) started either from a clone of this repository with `--setting-sources project`, or from an empty directory with `--setting-sources local` and a `--settings` file.
- Fixtures were created by the verifier in the session scratchpad and deleted afterwards:
  - local bare repositories with a symbolic link, an LFS pointer file, an executable file named `post-checkout` at the root, and a commit without an author email;
  - marker scripts that only append a fixed string to a file (hooks, templates, fsmonitor, smudge filter, `ext::` command, askpass);
  - a marker credential helper that reads and discards stdin and records only the operation name;
  - global configuration files selected with `GIT_CONFIG_GLOBAL`.
- The operator's global and system Git configuration were not modified. Inherited configuration was inspected by key name, plus the values of `http.sslbackend`, `core.symlinks`, `credential.helper`, and `filter.lfs.process`.
- Network probes used a private repository provided by the maintainer, a nonexistent repository under the same owner, and `octocat/Hello-World` as a public control.
- Evidence comes from harness tool results, exit codes, marker files, `GIT_TRACE` files, hook logs, and stream-json transcripts. All captured output, traces, and clone configuration were scanned for GitHub token patterns. The count was 0. Environment variables were checked only for presence or for comparison with a fixed value, and their values were never printed.

#### 8.1.1 Output handling

| Behavior | Status | Method | Result |
|---|---|---|---|
| Shell output limit | Verified | Bash commands printing exactly 30,000 and 30,001 characters, and Bash and PowerShell commands printing 20 KB to 2 MB of numbered lines | Up to 30,000 characters are returned inline (threshold measured in Bash). Above that, in both shells, the result shows an "Output too large" notice, the file path, and a 2 KB preview. |
| Saved output | Verified | Size and tail of the saved files | The complete output is saved to `~/.claude/projects/<project>/<session>/tool-results/<id>.txt`. A 2,000,018-byte output was saved without truncation. |
| stderr | Verified | Python probe writing stderr lines between flushed stdout blocks | stderr is merged into the same result and saved file, interleaved at flush points. |
| Line endings | Verified | Byte counts of saved files | Redirected Python stdout on Windows and PowerShell output are saved with CRLF line endings. |
| `Read` of saved output | Verified | `Read` with `offset` and `limit` on the 2 MB saved file | Readable, although it is outside the working directories. |
| `Read` size limit | Verified | `Read` without parameters on 500 KB and 2 MB files | Refused above 256 KB, with a message to use `offset` and `limit`. `allow_large` was not exercised on large files. |
| `Read` long lines | Verified | `Read` of a single 100,093-character line | Lines are not truncated individually. The result is capped at 25,000 tokens: 21,331 characters were returned, with a notice that a single long line cannot be paginated by line. |
| `Read` long files | Verified | `Read` without parameters on a 2,500-line file, and `offset`/`limit` near the end of a 119,256-line JSON file | All 2,500 lines were returned. The 2,000-line default in the tool description was not observed. Line-structured files page correctly with `offset` and `limit`. |
| `Read` of a file written by Python | Verified | Python wrote the same 50 KB file to three locations | (a) System temporary directory: refused by `blockReadsOutsideWorkingDirectories`. (b) Session scratchpad: readable. (c) Git-ignored `.cache/` under the agent root: readable, and `git status --ignored` reports it as ignored. |

Transport viability for an evidence document:

| Transport | 50 KB | 500 KB | 2 MB | Constraints and security trade-off |
|---|---|---|---|---|
| stdout in one call | Viable through the saved file | Viable through the saved file | Viable through the saved file | Every size above 30,000 characters is saved by Claude Code and must be read back with `Read`. The document must be line-structured: single-line JSON above the 25,000-token cap cannot be paginated. stderr diagnostics are interleaved with stdout. The saved file stays in Claude Code's project data under the user profile, outside the clone lifecycle and outside any cleanup by the collector. |
| Chunked stdout | Viable | Viable | Viable | Each chunk must stay at or under 30,000 characters to be inline, which takes at least 2, 17, and 67 calls respectively. Each call is a new process, so either the collector clones again for each chunk or it keeps the evidence between calls, which is a file. |
| File in the system temporary directory | Not viable | Not viable | Not viable | `Read` refuses it. |
| File in the session scratchpad | Viable | Viable | Viable | Readable. Outside the repository, so it cannot be committed. The scratchpad path is session-specific and is not a stable input for a deterministic tool. |
| File in a git-ignored directory under the agent root | Viable | Viable | Viable | Readable, and also reachable by `Grep`, `Glob`, and later sessions. Target content can be committed if the ignore rule changes or with `git add -f`, and it persists until deleted. |

#### 8.1.2 Process lifecycle

| Behavior | Status | Method | Result |
|---|---|---|---|
| Default timeout | Verified | Bash command without `timeout` | 120 seconds. |
| Maximum timeout | Partially verified | `timeout: 700000` on a short command | Accepted without error. The documented maximum is 600,000 ms. Clamping was not measured. `BASH_DEFAULT_TIMEOUT_MS` and `BASH_MAX_TIMEOUT_MS` are documented configuration variables. They are not set on the operator's machine and were not changed. |
| Tool timeout | Verified | `lifecycle.py` under `uv run`, with `timeout: 10000` in Bash and in PowerShell | The process is **not terminated**. It is moved to the background and keeps running (heartbeats continued). |
| `TaskStop` of a background command | Verified | `TaskStop` on the timed-out commands and on a `run_in_background` command, with `uv run python` and with `python` directly | The whole process tree is terminated: Python, the `git` child, and its `sh` grandchild. No `finally`, `atexit`, or signal handler marker was written. |
| User interrupt (Esc) | Verified | The maintainer pressed Esc during a foreground `uv run python` probe | Same as `TaskStop`: the tree was terminated after 16 seconds, without `finally`, `atexit`, or signal handler markers. |
| Python cleanup controls | Verified | Normal exit, and `signal.raise_signal` for `SIGINT` and `SIGTERM` | Handlers, `finally`, and `atexit` ran. |
| Child processes after `Popen.terminate()` | Verified | `finally` called `terminate()` and `wait()` on the `git` child | `C:\Program Files\Git\cmd\git.exe` is a launcher. Terminating it left the real `git.exe` and its `sh.exe` running as orphans that kept writing until they were stopped manually. |
| Commands left running when Claude Code exits | Not verified | — | Changed in 2.1.316 ([Section 11.1](#111-operating-versions)). Not probed, because the verifying session cannot exit. |

#### 8.1.3 Permission behavior

| Behavior | Status | Method | Result |
|---|---|---|---|
| `uv` invocation forms | Verified | Headless session from a clone of this repository, with the committed rules and `--allowedTools Bash PowerShell` | Required approval and were not executed: `uv run python <script>`, `uv run --project <root> python <script>`, `uv --project <root> run …`, `uv.exe run …`, `uv run --project <pkg> <console-script>`, `uvx --from <pkg> <console-script>`, the absolute path of `uv.exe`, the absolute path of a virtual environment's `python.exe`, and `uv run` in PowerShell. |
| Console-script executable | Verified | The same session, with a throwaway package in the scratchpad | `<venv>\Scripts\<console-script>.exe` invoked directly ran **without approval** in Bash and PowerShell, because no rule matches it. |
| Subprocesses of an approved Python process | Verified | `git config --global --get <fixture key>` (with `GIT_CONFIG_GLOBAL` pointing to a fixture) and `gh variable list -R <nonexistent>` | Run directly, both were denied in Bash and PowerShell by the existing deny rules. Run as subprocesses of an approved `uv run python` process, both ran: git printed the fixture value, and gh reached the API and received a 404. |
| Hook visibility | Verified | Headless session from an empty directory with a `PreToolUse` hook on `Bash\|PowerShell` that logs each command | The hook log contained only the two tool calls (the denied `git config` and `python`). The `git` and `gh` subprocesses did not appear. Permission rules and `PreToolUse` hooks, including the future hooks H-1 to H-6, do not see processes started by an approved process. |

#### 8.1.4 Authentication

Each scenario ran with `GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never`, unless the scenario states otherwise.

| Behavior | Status | Method | Result |
|---|---|---|---|
| Inherited system helper | Verified | `git ls-remote` of the private repository without a helper reset | Git Credential Manager from the system configuration ran (`git credential-manager get` in `GIT_TRACE`) and failed fast with "Cannot prompt because user interactivity has been disabled". |
| GUI prompt control | Verified | The same, with `GCM_INTERACTIVE` unset | GCM opened a dialog. The maintainer cancelled it, and GCM reported "User cancelled dialog" after 9.6 seconds. `GIT_TERMINAL_PROMPT=0` does not prevent GCM dialogs. |
| URL-scoped helper | Verified | Isolated global fixture with `credential.https://github.com.helper` set to the marker helper, in the shape written by `gh auth setup-git`, without a reset | The marker recorded `get`, and Git failed fast with "terminal prompts disabled". |
| `gh` helper clone | Verified | `git -c credential.helper= -c "credential.helper=!gh auth git-credential" clone` of the private repository, with and without `GIT_CONFIG_NOSYSTEM=1` | Succeeded without a prompt in 1.5 to 1.8 seconds. `GIT_TRACE` showed only `gh auth git-credential get`, then `store` after success. Neither GCM nor the URL-scoped marker ran. |
| Nonexistent repository | Verified | The same command for a nonexistent repository | Failed in 0.6 seconds with "Repository not found". `gh` was called with `get` only. No prompt and no `erase`. |
| Public control | Verified | The same command for `octocat/Hello-World` | Succeeded without any credential helper call, which shows that a public repository does not exercise the helper. |
| `core.askPass` and `GIT_ASKPASS` | Verified | No helper, marker askpass in `core.askPass` or `GIT_ASKPASS` | Both ran despite `GIT_TERMINAL_PROMPT=0`, once for the username and once for the password. `GIT_ASKPASS` takes precedence over `-c core.askPass=`. |
| Suppressing askpass | Verified | `-c core.askPass=` with `GIT_ASKPASS` empty, and with `SSH_ASKPASS` set to the marker | No askpass ran, including the `SSH_ASKPASS` fallback, and Git failed fast. |
| Shell tool environment | Verified | Presence and fixed-value checks | The PowerShell tool environment sets `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`, and an empty `GIT_ASKPASS`. The Bash tool environment sets none of them. |
| Clone configuration | Verified | Key names of `.git/config`, URL hosts, and whether URLs contain userinfo | No credential, credential helper, or `extraheader` key. `remote.origin.url` has host `github.com` and no userinfo. The remote remains configured. |

#### 8.1.5 Clone hardening

Local fixtures were cloned through `file://` with `protocol.file.allow=always` where a scenario needed it. LFS was neutralized in scenarios that test another setting, so that each setting is the only variable. Each control scenario left its marker or artifact.

| Setting | Status | Control | Hardened result |
|---|---|---|---|
| `core.symlinks=false` | Verified | `core.symlinks=true` created a symbolic link | The link was checked out as a regular file. System configuration already sets `false`. |
| `core.hooksPath=` (empty) | Verified | A `post-checkout` hook in an inherited `core.hooksPath` ran | No hook ran. `git rev-parse --git-path hooks/post-checkout` resolves to `/post-checkout`, the root of the current drive, so an empty value redirects hook lookup instead of disabling it. `/dev/null` resolves to `/dev/null/post-checkout`, and no hook ran with it either. A hook at the drive root was not planted. |
| `--template=` (empty) | Verified | An inherited `init.templateDir` copied a marker file and a `post-checkout` hook into `.git`, and the hook ran | Nothing was copied and `.git/hooks` was empty. With an empty `core.hooksPath` alone, the template hook was copied but did not run. |
| `core.fsmonitor=false` | Verified | An inherited `core.fsmonitor` hook ran during `git status` in the clone, and not during the clone itself | No fsmonitor call. A later `git status` without the option in the hardened clone called the hook again. |
| LFS smudge disabled | Verified | A marker smudge filter ran for the pointer file | `-c filter.lfs.smudge= -c filter.lfs.process= -c filter.lfs.required=false` checked out the pointer text and ran no filter. `GIT_LFS_SKIP_SMUDGE=1` does not stop a smudge command that is not git-lfs. |
| System git-lfs | Verified | Clone with the inherited system configuration | git-lfs ran as `filter-process`, failed to download the fixture object, and failed the checkout. It also wrote `pre-push`, `post-checkout`, `post-commit`, and `post-merge` hooks into `.git/hooks`, even with `--template=`. In a hardened clone, a later `git status` without the LFS options ran git-lfs again, and git-lfs wrote `lfs.repositoryformatversion` into the clone's configuration. |
| `protocol.allow=never`, `protocol.https.allow=always` | Verified | `protocol.ext.allow=always` ran an `ext::` marker command | `file://`, a plain local path, `ext::`, and SSH were refused with "transport '<name>' not allowed". HTTPS worked. |
| `transfer.fsckObjects=true` | Verified | The malformed commit was cloned without the option | Rejected with "missingEmail" and "fsck error in packed object". A plain local path clone with the option succeeded, because local clones do not transfer a pack. |
| `insteadOf` detection | Verified | Global fixture rewriting `https://github.com/` | `git ls-remote --get-url` printed the rewritten URL. `GIT_TRACE` showed no transport, and it succeeded with an unreachable proxy, so it needs no network. The hardening options do not neutralize the rewrite: `ls-remote` with all of them still contacted the rewritten host. |
| Combined hardening | Verified | A global fixture with every marker | Without options, the hooks path, smudge, and fsmonitor markers fired, and the template artifact and a symbolic link were created. With all hardening options and `--template=`, no marker fired and `.git/hooks` was empty. |

#### 8.1.6 Configuration isolation

| Behavior | Status | Method | Result |
|---|---|---|---|
| Neutralization | Verified | `GIT_CONFIG_NOSYSTEM=1` with an empty `GIT_CONFIG_GLOBAL` fixture | `git config --list` was empty. No inherited helper, hooks path, template, filter, fsmonitor, or `insteadOf` rewrite applied. Git's built-in sample hooks were still copied. |
| HTTPS on Windows | Verified | `ls-remote` of the public control and clone of the private repository under isolation | Both succeeded. System configuration sets `http.sslbackend=schannel` and no `http.sslCAInfo`. Under isolation the setting is absent, and the default backend verified `github.com` on this installation. |
| Proxy settings | Verified | Global fixture with `http.proxy` pointing to an unreachable port, and `HTTPS_PROXY` set to the same port under isolation | The configured proxy is dropped with the file that defines it. The environment proxy still applies under isolation. The operator's configuration defines no proxy, so dropping a real one is not applicable on this machine. |

#### 8.1.7 Cleanup on Windows

| Behavior | Status | Method | Result |
|---|---|---|---|
| Read-only Git files | Verified | `shutil.rmtree` on a fresh `--no-local` clone | Failed with `PermissionError` (WinError 5) on the read-only `.idx` file. The `.pack`, `.idx`, and `.rev` files are read-only. The removal was partial. |
| `onexc` handler | Verified | Handler that clears the read-only attribute and retries | Removed the clone. `onexc` requires Python 3.12. |
| `onerror` handler | Verified | The same logic with `onerror` | Removed the clone. Available in every supported Python version and deprecated since 3.12. No `DeprecationWarning` was emitted at runtime on 3.13.2. |
| File held open by another process | Verified | Another process held a worktree file open for 3 seconds | `PermissionError` (WinError 32) after a partial removal. Bounded retries (10 × 0.5 seconds) succeeded on attempt 7 after the file was released. |
| Lock longer than the retry budget | Verified | The same with a 15-second lock | All retries failed explicitly and the directory remained until the holder ended. |
| Python 3.12 | Not verified | — | Only Python 3.13.2 is installed. The minimum versions come from the Python documentation. |

#### 8.1.8 Findings for the acquisition design (#15)

Each finding is input for #15. None of them is a decision.

1. **`core.hooksPath` override.** [Security policy Section 5.3](security-policy.md#53-git) classifies overriding `core.hooksPath` to skip hooks as P. Every tested way to keep inherited hooks from running in a clone overrides it, and an empty value resolves hooks at the drive root instead of disabling them. Configuration isolation avoids inherited hooks without overriding the setting, but Git's built-in sample hooks are still copied unless `--template=` is used.
2. **Subprocesses are invisible to L2.** Permission rules and `PreToolUse` hooks see only the command that Claude runs. Every `git` and `gh` subprocess of an approved Python process runs unevaluated, so hooks H-1 to H-6 cannot control them. Their safety depends on the collector's own fixed arguments.
3. **Invocation form.** A console-script executable invoked directly matches no rule and is decided by the `auto` mode classifier. The `uv run` forms require approval.
4. **No cleanup on termination.** Tool timeouts do not terminate the process. `TaskStop` and user interrupts terminate the process tree without running `finally`, `atexit`, or signal handlers. A clone can therefore remain after an interrupted collection. Time limits must be enforced inside the collector.
5. **Orphaned Git processes.** `Popen.terminate()` on Git for Windows terminates only the launcher. The real `git.exe` and its children keep running and can hold files open in the clone.
6. **Removal constraints.** `shutil.rmtree` needs a read-only handler on Windows, fails partially on open files, and needs bounded retries and an explicit failure report. Under [security policy Section 5.1](security-policy.md#51-filesystem), the recursive deletion of the clone is X, and writing outside the agent environment is S.
7. **Transport constraints.**
   - Output above 30,000 characters is saved by Claude Code under the user profile, which stores target-derived evidence outside the collector's control.
   - `Read` caps each result at 256 KB and 25,000 tokens, and cannot paginate a single long line.
   - stderr is interleaved with stdout, and output uses CRLF.
   - The system temporary directory is not readable.
8. **Inherited configuration.**
   - System GCM, the system LFS filter, and URL-scoped helpers run unless they are neutralized.
   - git-lfs writes hooks into the clone and runs again on later commands.
   - `-c` options are not persisted, so every later Git command in the clone needs them again or needs isolation.
   - `insteadOf` rewrites are neutralized only by isolation, and `ls-remote --get-url` detects them without network access.
9. **Non-interactive behavior.**
   - `GIT_TERMINAL_PROMPT=0` does not suppress GCM dialogs or askpass programs.
   - The Bash tool does not set the variables that the PowerShell tool sets, so the collector cannot rely on the calling shell.
10. **Test fixtures for #17.** A plain local path clone skips `transfer.fsckObjects`, and the protocol restrictions refuse local transports. Tests with local bare repositories need `file://` and an explicit `protocol.file.allow`.
11. **Clone state after cloning.** The `gh` helper receives `store` after a successful clone. The effect of `store` in `gh` was not observed. `remote.origin.url` remains in the clone configuration until the collector removes it.

The decisions of #15 that consume each finding:

| Finding | Decision |
|---|---|
| 1. `core.hooksPath` override | `core.hooksPath` is never set; configuration isolation and an empty template ([ADR-0006 Section 6](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#6-configuration-isolation-and-hardening)) |
| 2. Subprocesses are invisible to L2 | Fully determined subprocesses and a closed command list ([security policy Section 5.7.3](security-policy.md#573-subprocess-rules)); RR-14 |
| 3. Invocation form | Module invocation through `uv run`, without console scripts ([ADR-0007 Section 1](adr/0007-python-invocation-contract.md#1-entry-point-and-invocation-form)); RR-15 |
| 4. No cleanup on termination | Internal deadline below the requested tool timeout; detection and explicit sweep ([ADR-0006 Section 3](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#3-single-process-lifecycle)); RR-16 |
| 5. Orphaned Git processes | No surviving descendant before removal, with the Windows mechanism chosen by #17 ([ADR-0006 Section 7](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#7-subprocesses-environment-and-process-termination)) |
| 6. Removal constraints | Read-only handling, bounded retries, and explicit failure. Removing the own workspace is a narrow S entry, and every other removal remains X ([security policy Section 5.1](security-policy.md#51-filesystem), [Section 5.7.5](security-policy.md#575-workspaces)). |
| 7. Transport constraints | A single line-structured, ASCII envelope on stdout with bounded lines and a total budget ([ADR-0006 Section 9](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#9-evidence-document-and-transport), [ADR-0007 Section 4](adr/0007-python-invocation-contract.md#4-envelope)); RR-17 |
| 8. Inherited configuration | Isolation through the subprocess environment for every Git command, and an isolation self-check ([ADR-0006 Section 6](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#6-configuration-isolation-and-hardening), [security policy Section 5.7.4](security-policy.md#574-subprocess-environment)) |
| 9. Non-interactive behavior | Fixed non-interactive variables and options, independent of the calling shell ([security policy Section 5.7.4](security-policy.md#574-subprocess-environment)) |
| 10. Test fixtures for #17 | An internal protocol parameter for local fixtures that the command-line interface never sets ([ADR-0007 Section 9](adr/0007-python-invocation-contract.md#9-subprocess-runner-seam)) |
| 11. Clone state after cloning | A bare repository without any remote; `store` is a verification point ([ADR-0006 Sections 4 and 5](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#5-acquisition-without-a-working-tree)) |

#### 8.1.9 Package invocation and resolution outcomes (#16)

Verified on 2026-11-01 for `repo resolve` (#16) with Claude Code 2.1.316, gh 2.92.0, Git 2.49.0.windows.1, Python 3.13.2, and uv 0.11.19 on Windows 11 Pro 10.0.26300, using the committed `.claude/settings.json`. `gh` is an MSI installation: the real `gh.exe` is in `C:\Program Files\GitHub CLI`, registered as a Windows Installer product, and not a shim. Git was not run by the package.

Method:

- **Invocation form.** A headless session (`claude -p --setting-sources project --permission-prompts none --output-format stream-json --verbose --allowedTools Bash PowerShell`) was started from a clone of the feature branch in the session scratchpad. It ran `uv run --locked python -I -m devops_tooling_agent repo resolve not/a/valid` once in each shell. This identifier is rejected offline, so the probe would have been harmless if it had run.
- **Editable installation.** The same form was run in the interactive session from this repository, through both shell tools.
- **`gh` outcomes.**
  - A probe script ran each `gh` command through the package runner, with the package's allowlisted environment. It recorded exit codes, durations, and the stderr of `gh repo view`. The output of `gh auth status` was never printed.
  - End-to-end invocations of `repo resolve` covered the same scenarios.
  - Missing authentication used a verifier-created empty `GH_CONFIG_DIR` in the scratchpad. The operator's `gh` configuration was neither read nor modified.
  - Network failure used `HTTPS_PROXY` and `https_proxy` set to the unreachable `http://127.0.0.1:9`, as in [Section 8.1.6](#816-configuration-isolation).
  - Network access by `gh --version` was measured with a local TCP listener set as the HTTPS proxy, which counted incoming connections.
  - A nonexistent repository was probed under `octocat` with a random name. `octocat/Hello-World` was the public control.
- All captured output and the stream-json transcript were scanned for GitHub token patterns. The count was 0. No environment value was printed.

| Behavior | Status | Result |
|---|---|---|
| Invocation form approval | Verified | Required approval in Bash and in PowerShell. Both calls are listed in `permission_denials` and were denied automatically; no `.venv` was created in the clone. The project `allow` rules were ignored because the clone was not trusted, as in V-10, which does not affect `ask` rules. |
| `-I` with the editable installation | Verified | The module was imported from the editable installation and wrote a complete envelope through both shell tools: exit code 0, no stderr, and no carriage return in the stdout bytes captured by Bash. |
| `gh --version` | Verified | Exit 0 in 0.08 seconds. The first line is `gh version 2.92.0 (2026-04-28)`. With the counting proxy, `gh --version` opened 0 connections, and the positive control (`gh repo view` through the same proxy) opened 1. With the unreachable proxy, it still exited 0. With the empty `GH_CONFIG_DIR`, it exited 0 and left the directory empty. Whether `--version` is processed before aliases and extensions was not verified. |
| `gh auth status --hostname github.com`, authenticated | Verified | Exit 0 under the allowlisted environment, with `GH_TOKEN` and `GITHUB_TOKEN` absent. Authentication came from the OS keyring. |
| `gh auth status`, missing authentication | Verified | Exit 1. |
| `gh auth status`, network failure | Verified | Exit 1, the same as missing authentication. **The exit code does not distinguish a network failure from missing authentication**, so `repo resolve` reports `AUTH_REQUIRED` for a network failure at this stage (known limitation, [`docs/python-package.md`](python-package.md#gh-commands)). The output is not parsed ([ADR-0006 Section 4](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#4-authentication)). |
| `GH_TOKEN` in the caller's environment | Verified | A fake `GH_TOKEN` with the empty `GH_CONFIG_DIR` gave `AUTH_REQUIRED`: the variable is not passed to `gh`. |
| `gh repo view github.com/<owner>/<repo>`, public control | Verified | Exit 0. The `HOST/OWNER/REPO` form is accepted. `id` is the legacy node ID form (`MDEwOlJlcG9zaXRvcnk…`), `visibility` is `PUBLIC`, `viewerPermission` is `READ`, `defaultBranchRef` is `{"name": "master"}`, and `diskUsage` is an integer. The JSON was 257 bytes. |
| `gh repo view`, nonexistent repository | Verified | Exit 1 in 0.46 seconds. stderr: `GraphQL: Could not resolve to a Repository with the name '<owner>/<repo>'. (repository)`. |
| `gh repo view`, missing authentication | Verified | Exit 4, the documented authentication exit code of `gh`, for both an existing and a nonexistent repository. |
| `gh repo view`, network failure | Verified | Exit 1. stderr starts with `Post "https://api.github.com/graphql": proxyconnect tcp:`. Classified as `PROVIDER_ERROR`. |
| `gh repo view`, inaccessible repository | **Not verified** | No existing repository that the account cannot access was available. **Assumption:** the outcome matches the nonexistent case, because GitHub does not reveal private repositories that the viewer cannot access. If it differs, it is reported as `PROVIDER_ERROR`, which fails closed. |
| End-to-end outcomes | Verified | `NOT_FOUND_OR_INACCESSIBLE` for the nonexistent repository, and `AUTH_REQUIRED` for missing authentication and for the network failure. `USAGE_ERROR` exited with 2, and `INVALID_IDENTIFIER` with `userinfo` did not echo the identifier. Every envelope was complete, without carriage returns or stderr bytes. |

Limitations:

- **Interim process termination on Windows (superseded).** At the time of this verification, the runner terminated only the direct child on Windows. #17 replaced it with a Job Object that contains every descendant ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)), which also covers `gh` shims of Scoop and Chocolatey. Only the MSI installation was available.
- Windows code paths were exercised only with Python 3.13.2. Python 3.12 and 3.14 run in CI on Linux.
- An empty repository was not available. The empty-repository rows of the default branch matrix ([`docs/python-package.md`](python-package.md#provider-values)) are covered by tests only.

#### 8.1.10 Workspace lifecycle and acquisition (#17)

Verified on 2026-11-01 for the workspace lifecycle, the acquisition lifecycle, and `workspace sweep` (#17) with Claude Code 2.1.316, Git 2.49.0.windows.1, gh 2.92.0 (MSI), Python 3.13.2 and 3.12.13, and uv 0.11.19 on Windows 11 Pro 10.0.26300. No other platform was available locally. CI runs the lifecycle tests with real Git on Linux with Python 3.12, 3.13, and 3.14.

Method:

- Probes were Python scripts in the session scratchpad that call the package's runner, workspace, and acquisition modules, run with `uv run --locked python -I`. Probe-only additions, such as `GIT_TRACE` or an injected configuration variable, were applied by a runner wrapper in the probe, never by the package.
- Fixtures were created by the verifier in the session scratchpad:
  - local bare repositories reached through `file://`, including one with a 150 MiB random blob;
  - an HTTP server on `127.0.0.1` that answers every request with 401;
  - a credential helper that only sleeps;
  - an empty `GH_CONFIG_DIR`, an empty CA bundle, and an empty certificate directory;
  - a directory to which `BUILTIN\Users` was granted full control, as a permissive base.
- Network probes contacted only `github.com`, for the public control `octocat/Hello-World`, which does not exercise the credential helper.
- Processes were observed through a Toolhelp snapshot of the probe's descendants and through their exit codes. Access control lists were read with `icacls`.
- Environment variables were checked only for presence or against a fixed value. All captured output and traces were scanned for GitHub token patterns. The count was 0.

| Behavior | Status | Result |
|---|---|---|
| Orphan reproduction, control | Verified | As in [Section 8.1.2](#812-process-lifecycle): `Popen.terminate()` on the `cmd\git.exe` launcher running a shell alias that appends to a marker left the real `git.exe` and `sh.exe` running, and the marker kept growing. |
| Job Object, option 1 of [ADR-0006 Section 7](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#7-subprocesses-environment-and-process-termination) | Verified | The same scenario through the runner: the child is created suspended, assigned to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and without breakaway, and resumed. The launcher, the real `git.exe`, and `sh.exe` were in the tree. After the timeout, none survived and the marker stopped growing. **Option 1 is the chosen mechanism.** Option 2 was not needed. |
| Credential helper under Git's shell | Verified | With the helper value `!'C:/Program Files/GitHub CLI/gh.exe' auth git-credential` and a fetch from the local 401 server, `GIT_TRACE` showed `start_command: 'C:/Program Files/Git/usr/bin/sh.exe' -c ''\''C:/Program Files/GitHub CLI/gh.exe'\'' auth git-credential get'`. Git runs the helper through its own `sh.exe`, and the single-quoted path with spaces works. `gh` ran with the empty `GH_CONFIG_DIR`, which stayed empty. |
| Helper containment | Verified | A helper that only sleeps, configured in the same quoted form, ran during a fetch from the local 401 server. `git.exe`, `git-remote-http.exe`, `sh.exe`, and `sleep.exe` were in the tree, and none survived the timeout. |
| Breakaway | Verified | A grandchild created with `CREATE_BREAKAWAY_FROM_JOB` was created without error, because the verifying process already runs inside an outer job, but it stayed in the runner's job and was terminated with it. |
| Termination time | Verified | Terminating the job and confirming that it has no active process took less than 1 ms after normal exits. A timed-out run returned within 0.07 seconds of its timeout. |
| Protocol restrictions with `init` and `fetch` | Verified | With the options of [security policy Section 5.7.3](security-policy.md#573-subprocess-rules), rule 9, a fetch from `file://`, a plain local path, `ext::`, and `ssh://` failed with `transport '<name>' not allowed`. |
| Object checks | Verified | A fixture commit without an author email was rejected during the fetch with `fsck error`, reported as `UNSUPPORTED_REPOSITORY` with `object_check_failed`. |
| Inherited hooks, templates, fsmonitor, and helpers | Verified | A marker configuration with `core.hooksPath`, `init.templateDir`, `core.fsmonitor`, and `credential.helper` in `HOME`, `XDG_CONFIG_HOME`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_COUNT`, and `GIT_CONFIG_PARAMETERS`, plus `GIT_TEMPLATE_DIR`, was in the package's source environment. No marker ran and no template artifact was copied. A positive control without isolation ran the `reference-transaction` hook and copied the template. |
| Repository configuration | Verified | After `init --bare --template=` and the fetch, the repository configuration contained only `core.repositoryformatversion`, `core.filemode`, `core.bare`, `core.symlinks`, and `core.ignorecase`. No remote, URL, helper, credential, or `extraheader` entry, no `FETCH_HEAD`, and no hooks directory. |
| Workspace size during the fetch | Verified | Polling every 0.2 seconds during a `file://` fetch of the 150 MiB blob observed `objects/pack/tmp_pack_*` growing from 9.2 MB to 154 MB over 0.8 seconds. With a 32 MiB limit, the fetch was terminated during the transfer at 43 MB observed, reported as `LIMIT_EXCEEDED`, and the workspace was removed. Sizes are read with `lstat`, not from cached directory entries. |
| TLS under isolation | Verified | The HTTPS fetch of the public control succeeded under isolation. `GIT_TRACE_CURL` showed the `schannel` backend, which is the default of this Git for Windows installation when `http.sslBackend` is not configured. |
| TLS trust variables | Verified | With `SSL_CERT_FILE` set to an empty bundle, or `SSL_CERT_DIR` set to an empty directory, the fetch still succeeded: `schannel` ignores both and trusts the Windows certificate store. Behavior with OpenSSL or other backends is not verified. |
| `gh auth git-credential store` and `erase` | Verified | In gh 2.92.0 (tag `v2.92.0`, commit `6c470f60803784e1558b626022677c53dccb6016`), `helperRun` in `pkg/cmd/auth/gitcredential/helper.go` returns before reading input or configuration for `store` and `erase`. With an empty, verifier-created `GH_CONFIG_DIR`, both exited 0 without output, with empty input and with a fake credential, and the directory stayed empty. That the OS keyring is unchanged rests on the source review. **No helper wrapper is needed.** |
| Removal on Python 3.12 | Verified | The full test suite passed on Windows with Python 3.12.13 and 3.13.2, including read-only pack files, symbolic links, and junctions inside a workspace, none of whose targets were touched. The package does not use `shutil.rmtree`, so `onexc` and `onerror` differences do not apply. |
| Path length on Windows | Verified (limitation) | With a temporary directory of about 120 characters, the fetch failed with `Filename too long` while writing `repository.git/objects/pack/pack-<id>.pack`. Git for Windows limits paths to 260 characters unless `core.longpaths` is set, which the package does not set. The workspace adds 54 characters and the longest pack path 78, so a temporary directory above about 125 characters fails. By the same budget, which was computed and not probed, a default branch name above about 140 characters would fail with this operator's default temporary directory (33 characters), because the fetched reference is written as a loose reference with a `.lock` file. Both fail closed as `PROVIDER_ERROR`. |
| `gh` helper and `gh auth status` through the OS keyring under the allowlisted environment | **Pending** | Requires the maintainer's disposable private fixture repository. |
| Helper with the absolute `gh` path authenticates | **Pending** | Requires the maintainer's disposable private fixture repository. |
| Only `get` and `store` helper calls during `init` and `fetch` | **Pending** | Requires the maintainer's disposable private fixture repository. |

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
- `blockReadsOutsideWorkingDirectories` does not stop reads through symbolic links inside an attached directory ([Section 8](#8-verified-behavior)). `Read` deny rules, including those for credential locations, still apply to the link's destination. The repository analysis skill reads only paths returned by `Glob`, which does not return links.
- Commands not covered by any rule are decided by the auto mode classifier.

## 10. Unverified behavior

The following are not established and MUST NOT be relied on:

| Behavior | Status |
|---|---|
| MCP behavior in interactive sessions | Partially observed: when the settings loaded into an interactive session, the seven denied GitHub MCP tools were removed from its context. Prompts for `mcp__*` are unverified. The GitHub plugin's MCP server did not connect in `claude -p` sessions. |
| Ask prompts and allow rules in interactive sessions under `auto` mode | Unverified. Documented behavior is that explicit ask rules always prompt; headless sessions deny instead of prompting. |
| `crossSessionInbound: "hold"` at runtime | Unverified. The value is accepted by `claude doctor`. |
| Impact of `blockReadsOutsideWorkingDirectories` on auto memory | Unverified. Documentation states that files Claude Code needs remain readable. The session scratchpad and saved tool output are readable ([Section 8.1.1](#811-output-handling)). |
| Commands left running when Claude Code exits | Unverified. Changed in 2.1.316. Relevant to clones left by an interrupted collection ([Section 8.1.2](#812-process-lifecycle)). Not relied on: a surviving collector ends at its internal deadline and removes its workspace, and a terminated one leaves a workspace for `workspace sweep` ([ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#3-single-process-lifecycle)). |
| Tool timeouts above 600,000 ms | Unverified. A larger value is accepted without error; whether it is clamped was not measured. Not relied on: the requested timeout stays at or below 600,000 ms ([`docs/python-package.md`](python-package.md#41-time)). |
| `Read` with `allow_large` on files above 256 KB | Unverified. Not exercised, to keep the session context bounded. Not relied on: collection results are paginated with `offset` and `limit`. |
| Remote analysis prerequisites on Linux and macOS | Partially verified. The lifecycle, runner, and sweep tests with real Git, including POSIX session termination and real signals, run in CI on Linux with Python 3.12, 3.13, and 3.14. Probes with real `gh` and network access ran on Windows only ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)). macOS remains unverified. |
| Execution of a hook at the drive root with an empty `core.hooksPath` | Unverified. The resolved path was verified ([Section 8.1.5](#815-clone-hardening)). Execution was not tested, because it requires writing to the drive root. Not relied on: `core.hooksPath` is never set ([ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#6-configuration-isolation-and-hardening)). |
| GCM with stored credentials for `github.com` | Unverified. GCM had no usable credential in the probes, so whether it would answer without a dialog was not observed. Not relied on: inherited helpers are reset and system configuration is isolated. |
| Configuration isolation on other Git for Windows installations | Unverified. HTTPS worked under isolation on this installation only, where the default backend is `schannel` ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)). An installation whose default backend differs, or that needs operator TLS or proxy settings, may fail. A failure is reported as `PROVIDER_ERROR` without fallback ([security policy Section 5.7.4](security-policy.md#574-subprocess-environment)). |
| `gh repo view` for an inaccessible repository | Unverified. No suitable repository was available ([Section 8.1.9](#819-package-invocation-and-resolution-outcomes-16)). Assumed to match the nonexistent case. Not relied on for safety: a different outcome is reported as `PROVIDER_ERROR`. |
| Invalid inactive account and `gh auth status` | Unverified. Whether an invalid inactive account on `github.com` changes the exit status of `gh auth status --hostname github.com`, which would make `repo resolve` report `AUTH_REQUIRED` although the active account is valid. Not probed, because it requires signing in to an isolated configuration. |
| `gh` process termination for shim installations | Not tested with a Scoop or Chocolatey shim, which was not available. The Job Object contains every descendant from its creation, verified with Git's launcher, which has the same structure: a launcher that starts the real program as a child ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)). |
| Default branch of an empty repository | Unverified. No empty repository was available. Whether `gh repo view` reports `defaultBranchRef` as `null` or with an empty name. Both are accepted, and a reported name is `PROVIDER_ERROR` ([`docs/python-package.md`](python-package.md#provider-values)). |
| `gh` resolution outcomes on Linux and macOS | Unverified. Only Windows was available. CI runs the tests with recorded outcomes on Linux. |
| Bare repository initialization and fetch | Partially verified ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)): no hook or template, object checks, protocol restrictions, and a repository configuration without URL, helper, or credential. That only `get` and `store` helper calls occur is pending the maintainer's disposable private fixture repository. Owner: #17. |
| OS keyring authentication under the allowlisted environment | Partially verified. `gh auth status` and `gh repo view` authenticated through the OS keyring on Windows with only the variables of [security policy Section 5.7.4](security-policy.md#574-subprocess-environment) ([Section 8.1.9](#819-package-invocation-and-resolution-outcomes-16)). The `gh` credential helper under that environment remains unverified. [Section 8.1.4](#814-authentication) was verified with the full inherited environment. Owner: #17. |
| Credential helper with an absolute `gh` path | Partially verified. Git runs the single-quoted absolute path through its own `sh.exe`, and the helper's processes are contained by the Job Object ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)). Authentication through it is pending the maintainer's disposable private fixture repository. Owner: #17. The `PATH` form is the fallback. |
| Process termination | Verified on Windows with the Job Object ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)). On POSIX, session termination and the empty process group are covered by tests that run in CI on Linux. |
| TLS trust variables under isolation | Partially verified. Git for Windows with `schannel` ignores `SSL_CERT_FILE` and `SSL_CERT_DIR` and trusts the Windows certificate store ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)). Behavior with OpenSSL and other backends, such as on Linux, is unverified. |
| Windows path length for workspaces | Known limitation, not a verification gap: paths above 260 characters fail without `core.longpaths`, which the package does not set ([Section 8.1.10](#8110-workspace-lifecycle-and-acquisition-17)). Whether to support longer temporary directories or branch names is not decided. |
| Requested tool timeout for package calls | Unverified. Whether a `timeout` of 300,000 ms is honored for `repo collect` in both shell tools. Owner: #19. |
| Loading of a target's `AGENTS.md` | Unverified. The `InstructionsLoaded` hook does not observe `AGENTS.md`. |
| Absolute program paths (`/usr/bin/git … push`) | A probe was blocked, but the behavior is undocumented and not relied on. |
| Model invocation when a target skill reuses a project skill name | Unverified. Both skills are listed to the model with their own descriptions, so a target description could attract model invocation. Invocation was not attempted. |
| `Skill(<directory-name>:<skill-name>)` deny rules | Not verified with the prefixed name. V-8 verified that a `Skill(<name>)` deny rule blocks a target skill. |

## 11. Re-verification

Supported Claude Code versions and re-verification are defined in [ADR-0005](adr/0005-claude-code-version-support-and-re-verification.md):

- **Minor or major version change:** re-verify [Section 8](#8-verified-behavior) to [Section 10](#10-unverified-behavior) in full. The result establishes a new verified version line.
- **Patch version change:** review every changelog entry after the most recent operating version, up to and including the new version. Re-verify the rows affected by entries that concern permission rules and their matching, settings and their precedence, shell and file tools, hooks, MCP, skills and subagents, instruction file loading, sandbox, or permission modes. Record the review in the table below. The human maintainer approves its conclusion in the pull request that records it.
- **Other changes:** re-verify the affected rows when `.claude/settings.json` changes, or when a new shell tool or MCP server is introduced.

A task that depends on verified behavior and runs on a version that is not listed below stops and reports the version.

Use only fixtures created for the verification, keep probes harmless if executed, and run probes that could expose the environment without user settings and with a minimal process environment.

### 11.1 Operating versions

Verified version line: **2.1**.

| Version | Date | Basis | Result |
|---|---|---|---|
| 2.1.314 | 2026-10-30 | Full verification ([Section 8](#8-verified-behavior)) | Verified |
| 2.1.316 | 2026-10-31 | Changelog review | No affected row. Operating version without re-verification. |

Changelog review of 2.1.316:

- Source: `CHANGELOG.md` of the public `anthropics/claude-code` repository on GitHub, read on 2026-10-31. The changelog has no 2.1.315 entry, so the review covers the 55 entries of 2.1.316.
- Entries that concern the reviewed areas, and why no row is affected:
  - PowerShell hooks not blocking on exit code 2 for some command forms: no hook is configured ([Section 2.3](#23-hooks)). Relevant to the future hooks of [policy Section 10](security-policy.md#10-security-hook-requirements).
  - Bash tool handling of aliases, functions, and `SHELLOPTS`, and Git Bash rc files on Windows: command execution, not permission matching.
  - Shell commands left running after Claude Code exits: process lifecycle. Not covered by Section 8, and in scope of the remote analysis verification (#14).
  - `--restricted` sessions uploading files outside the working directories: the mode is not used.
  - `autoMode.allow` lists without `"$defaults"`, and auto mode verdicts and proxies: the project settings define no auto mode rules, and the auto mode classifier is not a security control ([policy Section 3](security-policy.md#3-enforcement-layers)).
  - Server-managed settings for Team and Enterprise logins: not available for the operator's account type ([Section 5.4](#54-project-settings-are-not-tamper-proof)).
  - Agent SDK requests loading `CLAUDE.md` outside the working directory: SDK sessions, not `--add-dir` in CLI sessions ([Section 8](#8-verified-behavior), instruction files).
  - `Grep` and `Glob` hanging on Windows: availability, not access control.
- The remaining entries concern models, gateways, IDE and cloud integrations, and the user interface.

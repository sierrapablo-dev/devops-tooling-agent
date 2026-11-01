# ADR-0006: Remote repository acquisition through deterministic collectors

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

The repository analysis skill (#7) inspects targets that the operator attaches to the session with `--add-dir`. The Remote Repository Analysis milestone requires `/analyze-repository <repository-identifier>` to analyze GitHub repositories without attached directories, while target content remains untrusted and target code is never executed.

Issue #14 verified the behavior that such a design depends on ([environment Section 8.1](../claude-code-environment.md#81-remote-analysis-prerequisites)). The findings that constrain this decision are:

- Permission rules and `PreToolUse` hooks see only the tool call. Subprocesses of an approved Python process run without evaluation ([8.1.3](../claude-code-environment.md#813-permission-behavior)).
- Tool timeouts move a process to the background instead of terminating it, and `TaskStop` or a user interrupt terminate the process tree without running `finally`, `atexit`, or signal handlers ([8.1.2](../claude-code-environment.md#812-process-lifecycle)).
- Terminating the Git for Windows launcher leaves the real `git.exe` and its children running ([8.1.2](../claude-code-environment.md#812-process-lifecycle)).
- Every tested way to keep inherited hooks from running overrides `core.hooksPath`, which the [security policy](../security-policy.md#53-git) classifies as P. Configuration isolation with `--template=` avoids inherited hooks, template hooks, filters, fsmonitor, helpers, and `insteadOf` rewrites without changing any global configuration ([8.1.5](../claude-code-environment.md#815-clone-hardening), [8.1.6](../claude-code-environment.md#816-configuration-isolation)).
- Delegating authentication to `gh` through a credential helper passed on the command line works without prompts. Inherited helpers, GCM dialogs, and askpass programs run unless they are neutralized explicitly, and the Bash tool does not set the non-interactive variables that the PowerShell tool sets ([8.1.4](../claude-code-environment.md#814-authentication)).
- Output above 30,000 characters is saved by Claude Code under the user profile. `Read` caps each result and cannot paginate a single long line, stderr is merged into the same result, and Windows output uses CRLF ([8.1.1](../claude-code-environment.md#811-output-handling)).
- Removing a clone on Windows needs a read-only handler and bounded retries, and can still fail while another process holds a file ([8.1.7](../claude-code-environment.md#817-cleanup-on-windows)).

[ADR-0004](0004-session-scoped-targets-and-native-provider-configuration.md) defines explicit, session-scoped target selection, and [ADR-0002](0002-python-as-deterministic-integration-layer.md) defines the Python package as the deterministic layer. The invocation contract of the package is recorded in [ADR-0007](0007-python-invocation-contract.md).

## Decision

This ADR records decisions and invariants. Initial operating values, such as time, size, and evidence limits and minimum tool versions, are documented in [`docs/python-package.md`](../python-package.md) and change through a reviewed pull request without a new ADR, provided that every invariant below still holds. Normative security rules, including the subprocess environment, are in [security policy Section 5.7](../security-policy.md#57-python-package-operations).

### 1. Collector model

- The Python package acquires remote repositories and extracts bounded evidence from them. Claude Code reasons only over that evidence and produces the existing evidence-based report.
- Collectors select and bound evidence. They do not interpret it, and they never execute target code.
- The package exposes three subcommands: `repo resolve`, `repo collect`, and `workspace sweep`. Their invocation contract is [ADR-0007](0007-python-invocation-contract.md), and their tiers are defined in [security policy Section 5.7](../security-policy.md#57-python-package-operations).
- Scope: repositories on `github.com`, at the tip of their default branch. Other hosts, other refs, submodule content, and LFS content are out of scope. Submodules and LFS pointer files are reported as entries of the repository tree only.

### 2. Identifiers and binding by node ID

- `repo resolve` accepts `https://github.com/<owner>/<repo>`, `git@github.com:<owner>/<repo>`, each with an optional `.git` suffix, and `<owner>/<repo>`. Validation is offline, strict, and ASCII-only. Owner names follow GitHub's account name rules, and repository names are limited to letters, digits, `.`, `_`, and `-`, excluding `.` and `..`. Every other form is rejected, including other hosts, `http://`, user information, ports, queries, fragments, percent-encoding, and extra path segments.
- A rejected identifier is never echoed, because it may contain a credential. Rejections report a reason from a fixed vocabulary.
- `repo resolve` queries GitHub through `gh` with an explicit host and returns the canonical identity: `owner/repo`, node ID, URL, visibility, default branch, viewer permission, archived and empty status, and disk usage. It also reports whether the input matches the canonical identity. A nonexistent repository and an inaccessible repository are a single outcome.
- The user confirms the canonical identity ([configuration Section 3.2](../configuration.md#32-target-selection)). `repo collect` accepts only the canonical `owner/repo` and the node ID that the user confirmed.
- `repo collect` resolves the repository again before it creates a workspace. If the node ID or the exact canonical name differs, it stops before acquisition.
- `repo collect` resolves the repository a third time after the fetch. If the node ID or name differs, the evidence is discarded and the invocation fails.
- The fetch URL is built only from the validated canonical identity, never from a URL returned by a tool or found in content.
- A window remains between the checks and the fetch in which a rename or transfer followed by a reverse change within seconds would go undetected (RR-18).

### 3. Single-process lifecycle

`repo collect` runs in one process, in this order:

1. Validate arguments offline, build the subprocess environment, and resolve executables.
2. Check authentication, resolve the repository, and verify the binding ([Section 2](#2-identifiers-and-binding-by-node-id)). Check the provider-reported disk usage against its limit.
3. Detect stale workspaces of other invocations and report them as warnings, without removing them ([Section 10](#10-workspaces-and-sweep)).
4. Create the workspace and its ownership marker.
5. Check that configuration isolation is effective ([Section 6](#6-configuration-isolation-and-hardening)).
6. Acquire the repository ([Section 5](#5-acquisition-without-a-working-tree)).
7. Verify the binding again.
8. Collect evidence.
9. Terminate any remaining descendant process and remove the workspace.
10. Emit the result.

Invariants:

- The result is emitted only after the removal attempt has finished, so that it always reports the cleanup outcome. Evidence is held in memory, within the evidence budget, until then.
- Any failure before step 4 leaves nothing on disk. Any failure after step 4 still runs step 9.
- A collector-internal deadline bounds the whole invocation. It is shorter than the tool timeout that the skill requests for the call, and that timeout does not exceed the documented maximum of the shell tools. Acquisition and collection stop early enough to leave a fixed reserve for step 9.
- When the deadline expires, the collector terminates its descendants, removes the workspace, and reports a timeout. A tool timeout therefore never leaves the collector running unbounded in the background.
- On `SIGINT` or `SIGTERM`, handlers run ([8.1.2](../claude-code-environment.md#812-process-lifecycle)), and the collector removes the workspace and reports the interruption.
- `TaskStop` and user interrupts terminate the process tree without cleanup. The workspace then remains until a later invocation detects it and the user removes it with `workspace sweep` (RR-16).
- If removal fails, the failure takes precedence over any earlier outcome, the earlier outcome is reported with it, and the evidence is discarded.

### 4. Authentication

- Authentication is delegated to `gh`. `gh auth status` for `github.com` checks it, and only its exit status is used.
- Git authenticates through the `gh` credential helper passed on the command line, after resetting inherited helpers, and only for the invocation that contacts GitHub ([8.1.4](../claude-code-environment.md#814-authentication)).
- The package never runs `gh auth token`, never reads `gh` configuration or any credential store, and never places a token in arguments, URLs, environment variables, or files.
- `GH_TOKEN` and `GITHUB_TOKEN` are removed from the environment of every subprocess. Authentication comes only from the credential storage of `gh`, which is a supply mechanism of [security policy Section 8.1](../security-policy.md#81-storage-and-supply). An operator who authenticates only through those variables receives an authentication error.
- Non-interactive behavior does not depend on the calling shell. Prompts, GCM dialogs, and askpass programs are disabled by the fixed environment and options of [security policy Section 5.7](../security-policy.md#57-python-package-operations).
- Git calls the helper with `store` after a successful fetch. Its effect is a verification point owned by #17. If `store` persists anything, #17 uses a helper wrapper that answers only `get`.

### 5. Acquisition without a working tree

- The repository is acquired into a bare repository that the same invocation initializes with an empty template, followed by a shallow fetch of the default branch from the canonical URL. No remote is ever configured, so no configuration change is needed after creation, and the repository configuration never contains a URL, a helper, or a credential.
- No working tree is created. Inventory comes from the tree of the fetched commit, and file contents come from blob objects. No path controlled by the target is written to disk, so symbolic links, filters, reserved Windows names, long paths, and planted executables have no effect.
- Recent commits come from the history within the fetch depth. The number of recent commits never exceeds the fetch depth.
- The default branch name is taken from the provider response and is accepted only if it matches a strict reference name grammar.
- Rejected alternatives are listed below. The behavior of this sequence was verified for `git clone`, not for `init` and `fetch`. Equivalence is a verification point owned by #17.

### 6. Configuration isolation and hardening

- `core.hooksPath` is never set. Inherited hooks are avoided by configuration isolation: system configuration is disabled, and global configuration points to an empty file that the workspace owns. The bare repository is created with an empty template, so it contains no hooks.
- Isolation is part of the subprocess environment, so it applies to every Git command of the invocation. Command-line options that are not persisted ([8.1.8](../claude-code-environment.md#818-findings-for-the-acquisition-design-15), finding 8) are built by a single function for every Git invocation.
- Before acquisition, the collector checks that Git reports no configuration outside the command-line scope and that the canonical URL is not rewritten. If either check fails, it stops.
- Transport is restricted to HTTPS, transferred objects are checked, and automatic maintenance is disabled so that no detached Git process outlives the command.
- Settings that only matter for a working tree, such as `core.symlinks`, `core.fsmonitor`, and LFS filters, are not set, because no command materializes or inspects a working tree.
- Isolation also drops operator Git settings for TLS and proxies. HTTPS on other Git for Windows installations is a verification point owned by #17. A failure is reported as a provider error. No fallback to operator configuration exists.

### 7. Subprocesses, environment, and process termination

- Every subprocess argument vector is determined by validated inputs and fixed constants. Subcommands never forward free-form arguments. No shell is used.
- `git` and `gh` are resolved once to absolute paths before any subprocess runs. The current directory is never part of that resolution.
- The subprocess environment is built from an allowlist. Every `GIT_*` variable is removed, and fixed values are added. The allowlist is normative and is defined in [security policy Section 5.7](../security-policy.md#57-python-package-operations).
- Standard input is closed for every subprocess. Output is captured and never forwarded verbatim.
- Git commands receive the repository location explicitly. Their working directory is never inside the repository data.
- After any collector-side termination (deadline, limit, or error), no descendant process of the collector remains, and removal starts only after that holds. On POSIX, subprocesses run in a new session and the session is killed. On Windows, #17 chooses the mechanism with this criterion: the first of the following that passes a reproduction of the orphan scenario of [8.1.2](../claude-code-environment.md#812-process-lifecycle) on the verified platform:
  1. A Job Object that contains every descendant from its creation.
  2. Per-child Job Object assignment with a tree-termination fallback.

  If neither passes, #17 stops and reports.

### 8. Limits

- A limit on provider-reported disk usage refuses a repository before any workspace is created.
- A limit on workspace size is enforced during the fetch. When it is exceeded, the fetch is terminated and the invocation fails.
- The fetch depth bounds history.
- Evidence limits bound tree entries, listed paths, file count, per-file size and lines, total content, and readable blob size. Exceeding an evidence limit truncates evidence and reports the truncation. It does not fail the invocation.
- Every limit and its initial value are documented in [`docs/python-package.md`](../python-package.md#4-operating-values).

### 9. Evidence document and transport

- The evidence document is the result of a single envelope ([ADR-0007](0007-python-invocation-contract.md)) written to standard output. Neither the scratchpad nor a git-ignored directory is used, and no evidence file is written by the package.
  - The scratchpad path is session-specific and is not a stable input for a deterministic tool.
  - A git-ignored directory would persist target content in the agent repository, and deleting ignored files is X.
  - The system temporary directory is not readable by `Read`.
- Output above the inline threshold is saved by Claude Code under the user profile and read back with `Read`. That copy is outside the collector's control and persists like the session transcript (RR-17).
- The document is line-structured. No serialized line exceeds a bound that is far below the `Read` result cap, so every part of the document can be read with `offset` and `limit`. The bound applies to every serialized string field, including paths, commit subjects, identifiers, messages, and file content lines. File contents are arrays of bounded lines.
- A value that does not fit is truncated, and the truncation is recorded structurally in the containing object, never only inside the string.
- The total envelope stays below a stated budget. When evidence exceeds it, sections are reduced in a fixed priority order that removes listed paths first and collector and provenance metadata last.
- Target-derived values are confined to a dedicated section that the envelope marks as untrusted data.

### 10. Workspaces and sweep

- A workspace is a new directory created by the invocation directly under the system temporary directory, with a project prefix and a random name. It is private to the operator. The collector refuses a temporary directory inside the agent environment.
- An ownership marker is created exclusively in the workspace before any Git command. It records a fixed identifier, a format version, and the creation time.
- Removal of the invocation's own workspace re-validates the marker, never follows symbolic links or junctions, handles read-only files, retries within a bounded budget, and reports failure explicitly.
- Private repository objects are stored unencrypted during the collection. A workspace left by an interrupted invocation keeps them until it is removed, and removal is not a secure erase (RR-16).
- `repo collect` only detects and reports stale workspaces of other invocations. Removing them is a separate, explicit operation: `workspace sweep` lists eligible workspaces, and `workspace sweep --remove <workspace-name>` removes exactly one.
- A workspace is eligible only when all of the following hold:
  - it is a real directory directly under the system temporary directory, not a link or junction;
  - its name matches the prefix pattern;
  - its marker is a valid regular file;
  - its age is above the sweep minimum age.
- The sweep minimum age is more than ten times the maximum collector runtime, so a live collection is never eligible.

### 11. Evidence allowlist and collector sources

- The Python package is the source of truth for what is collected: evidence file patterns, sensitive file names, excluded directories, agent component names, and limits. [`inspection-guide.md`](../../.claude/skills/analyze-repository/inspection-guide.md) remains the source of truth for how evidence is interpreted.
- Sensitive files are reported by path only, and their content is never read. Agent instruction files and Claude Code components of the target are reported by presence only.
- Until #20 replaces the lists in `inspection-guide.md` with a reference to the package, a test introduced by #18 requires the sensitive file names and excluded directories of the guide to equal the package constants. It also requires the sensitive file names to cover the project secrets of [security policy Section 8.3](../security-policy.md#83-credential-locations).
- Collectors consume a source-neutral tree: entry path, type, size, and a bounded content reader. Remote analysis uses a Git object source. Local paths (#20) add a filesystem source under the classification and invariants of [security policy Section 5.7](../security-policy.md#57-python-package-operations). Its mechanics are decided in #20.
- Evidence from allowlisted files can contain literal secrets, because collectors do not scan for secrets. Name-based exclusion protects only sensitive file locations (RR-17).

### 12. Relationship to ADR-0004

This decision **extends** ADR-0004 and does not supersede it. Each decision of ADR-0004 still holds:

- Selection is explicit and session-scoped. A remote identifier that the user supplies and confirms after resolution is a new form of explicit selection that needs no attached directory ([configuration Section 3.2](../configuration.md#32-target-selection)). An identifier found in untrusted content is not a selection.
- Target facts are derived from evidence and not stored. Resolution through `gh` is the independent confirmation that ADR-0004 requires for verified provider facts.
- Targets contain no agent configuration, and no configuration file is introduced. Operating values are constants of the package.
- Authentication stays in the native mechanism of `gh`. The Python integration delegates to it and never receives credentials, as [configuration Section 4.5](../configuration.md#45-adding-a-provider-integration) requires.

### Verification points

The decisions above rely on the following behavior, which is not yet verified. Each point is listed in [environment Section 10](../claude-code-environment.md#10-unverified-behavior) with its owning issue:

- **#17:**
  - `init` and `fetch` behave as the verified `clone`.
  - The `gh` helper and `gh auth status` authenticate through the OS keyring under the allowlisted environment on Windows.
  - The helper works with the absolute path of `gh`.
  - The effect of `gh auth git-credential store`.
  - No descendant survives termination.
  - No detached maintenance runs.
  - The isolation self-check works.
  - The workspace size limit applies during the fetch.
  - Removal works on Python 3.12.
  - The temporary directory is private to the operator on Windows.
  - TLS works under isolation and with the allowlisted trust variables.
- **#19:** the requested tool timeout is honored by both shell tools.

## Alternatives considered

- **Attaching a local clone with `--add-dir`:** requires the operator to clone manually, exposes the clone to file tools and to the target's skills, and leaves cleanup to the operator.
- **Several processes (clone, then collect, then remove):** a clone would survive between calls, and each call would need its own approval and identity binding.
- **Checkout with hardening:** verified, but writes target-controlled paths to disk and depends on settings that matter only because a working tree exists.
- **`git clone --bare` followed by removing `origin`:** equivalent acquisition, but requires a repository-local configuration change, which is S under [security policy Section 5.3](../security-policy.md#53-git).
- **Overriding `core.hooksPath`:** prohibited (P), and an empty value redirects hook lookup instead of disabling it.
- **Inheriting the environment and removing known variables:** a denylist cannot anticipate every variable that injects configuration or changes behavior.
- **Passing `GH_TOKEN` and `GITHUB_TOKEN` through:** environment tokens are not a supply mechanism of the security policy.
- **Chunked stdout, scratchpad files, or a git-ignored directory as transport:** see [Section 9](#9-evidence-document-and-transport).
- **Single-line JSON output:** cannot be paginated by `Read` above its cap.
- **Automatic sweep inside `repo collect`:** deleting data left by another invocation is X and needs individual confirmation.
- **Recent commits through the GitHub API:** adds another network path and parser for data that a shallow fetch already provides.

## Consequences

- **Positive:**
  - Remote repositories are analyzed without attaching directories, without executing target code, and without writing target-controlled paths to disk.
  - No inherited hook, filter, helper, or rewrite can run, and no prohibited configuration change is needed.
  - Every subprocess is fully determined by validated inputs and constants, which compensates for subprocesses being invisible to permission rules and hooks.
  - The same collectors can serve local paths, which gives the evidence allowlist a single source of truth.
- **Negative:**
  - The package runs `git` and `gh` without L2 evaluation (RR-14). Its own rules and tests are the control.
  - An interrupted collection can leave private repository content in the system temporary directory until the user removes it (RR-16).
  - Evidence above the inline threshold persists in Claude Code's project data under the user profile (RR-17).
  - Operator Git settings for TLS, proxies, and credentials are deliberately ignored. Some installations may need an amendment of the environment allowlist.
  - Several behaviors are verification points for #17 and #19, and the design is verified on Windows only.

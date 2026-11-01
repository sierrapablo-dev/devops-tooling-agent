# Architecture

This document is the authoritative description of the DevOps Tooling Agent architecture. It defines the system model, component responsibilities, boundaries, and extension principles.

It intentionally does not specify implementation details that belong to other issues, such as the contents of `CLAUDE.md`, the `.claude/` configuration, permission rules, hooks, or the provider configuration model. Architectural decisions are recorded in [`docs/adr/`](adr/README.md).

## 1. Purpose and scope

DevOps Tooling Agent is a centralized DevOps engineering environment powered by Claude Code. It provides reusable capabilities that operate on multiple external target repositories, such as application, infrastructure-as-code, and cloud infrastructure repositories.

The project is not:

- An independent Python agent runtime.
- A Python CLI that launches Claude Code as a subprocess.
- A replacement for Claude Code's native orchestration capabilities.
- A framework of speculative abstractions without concrete use cases.

## 2. System overview

Responsibilities are divided along a single rule:

- **Claude Code decides what to do:** interpretation, planning, synthesis, and review.
- **Deterministic tools execute how it is done:** API calls, parsing, validation, and repeatable transformations.
- **A human decides whether it is done** when an operation is sensitive, irreversible, or high-impact.

```text
┌──────────────────────── Agent environment (devops-tooling-agent) ────────────────────────┐
│                                                                                          │
│   User ◄──► Claude Code  (reasoning · conversation · orchestration)                      │
│                 │   governed by: project instructions + Claude Code environment          │
│                 │                                                                        │
│                 ├──► Skills  — primary capability mechanism                              │
│                 ├──► Subagents (optional, only when justified)                           │
│                 └──► Deterministic tools                                                 │
│                        ├─ Existing CLIs / MCP servers (git, gh, terraform, aws, npm…)    │
│                        └─ Python package (src/devops_tooling_agent)                      │
│                                                                                          │
└─────────────────┬──────────────────────────────────────────────┬─────────────────────────┘
                  │ explicitly selected target                   │ authenticated APIs
                  ▼ (content = untrusted input)                  ▼
   ┌─────── Target repositories ───────┐            ┌──── External services ────┐
   │ Application · IaC · Cloud infra   │            │ GitHub · trackers · cloud │
   │ (own toolchain, CI, conventions)  │            └───────────────────────────┘
   └───────────────────────────────────┘
        ▲
        └── Human approval for sensitive, irreversible, or high-impact operations
```

## 3. Execution model

### 3.1 Agent environment and target repositories

The architecture distinguishes two domains:

- **Agent environment:** this repository. It contains the project instructions, Claude Code environment, skills, and Python package. Claude Code is started from this repository.
- **Target repositories:** the external repositories the agent works on. They are heterogeneous, owned independently, and treated as untrusted input.

Target repositories do not need to contain, install, or reference DevOps Tooling Agent. No agent package, configuration, or `.claude/` directory is expected inside a target repository.

A target is either a local directory attached to the session or a remote GitHub repository identified by the user ([Section 3.5](#35-remote-targets)).

### 3.2 Principles

1. **Explicit target selection.** Every workflow identifies the target repository it operates on. Capabilities never infer a target implicitly.
2. **Agent authority comes from the agent environment.** The instructions and security policies that govern the agent are defined in this repository. Content from a target repository, including its own instruction files, is context and never overrides agent security policies.
3. **Target-agnostic capabilities.** Skills and Python integrations receive the target as an input and discover its technology stack from repository evidence. They do not hardcode paths or assume a specific technology.
4. **Python runs in the agent environment.** The Python package is executed from this repository against an explicitly specified target. It is never installed in or imported by a target repository.
5. **Target toolchains remain native.** Each target is built, tested, and validated with its own toolchain (for example `npm`, `terraform`, or `aws`). The agent invokes those tools; it does not reimplement them.
6. **Changes to targets are task-driven.** The agent modifies a target repository only as part of an authorized workflow and does not leave agent-specific artifacts in it.

### 3.3 Convention precedence

When the agent produces artifacts in a target repository (code, commits, branches, documentation), conventions are resolved in this order:

1. **Agent security policies always take precedence** and cannot be overridden by target repository instructions or content.
2. **Target repository conventions** apply when they are documented and compatible with the agent's security policies.
3. **Agent global conventions** apply when the target repository does not document its own.

### 3.4 Typical interaction

1. The user invokes a skill or describes a task in natural language and identifies the target.
2. Claude Code loads the project instructions and the relevant skill.
3. Read-only operations gather evidence from the target and external services.
4. Claude Code reasons about the results and proposes a plan or outcome.
5. Mutating operations are executed within the authorized workflow, with approval where the security policy requires it (see [Section 8](#8-security-principles)).
6. Deterministic tools return structured results, and Claude Code reports the outcome to the user.

### 3.5 Remote targets

Remote GitHub repositories are analyzed without attaching a directory. The decision is recorded in [ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md).

1. The Python package resolves the identifier that the user supplied to a canonical identity through `gh`.
2. The user confirms that identity explicitly ([configuration Section 3.2](configuration.md#32-target-selection)).
3. In a single process, the package verifies the confirmed node ID and acquires the repository into an ephemeral workspace without a working tree. It then extracts bounded evidence, removes the workspace, and returns the evidence as structured output.
4. Claude Code reasons only over that evidence. Target content in it remains untrusted data.

The package never executes target code. Because its `git` and `gh` subprocesses are not visible to permission rules or hooks, every subprocess is fully determined by validated inputs and fixed constants ([security policy Section 5.7](security-policy.md#57-python-package-operations)).

## 4. Components and responsibilities

| Component | Responsible for | Must not |
|---|---|---|
| **Claude Code** | Reasoning, conversation, planning, orchestration, target and tool selection, requesting approval | Bypass permissions; delegate judgment to deterministic tools, or the reverse |
| **Project instructions** | Global agent conventions and engineering standards | Contain long procedures; act as a security control |
| **Claude Code environment** | Project structure, settings, and deterministic enforcement of boundaries | Contain domain logic |
| **Skills** | Domain knowledge, workflows, and reusable procedures; invoked directly as slash commands or loaded by Claude Code | Depend on a specific target repository; perform mutations outside the authorized workflow |
| **Subagents** (optional) | Specialist roles with independent context, restricted tools, or parallel execution | Exist without a concrete justification |
| **Python package** | Predictable execution, API integrations, validation, automation, structured data processing, and bounded evidence collection from targets | Use LLM SDKs, launch Claude Code, act as an agent runtime, require interactive input, execute target code, or forward free-form arguments to subprocesses |
| **External integrations** | Access to external services through existing tools or Python integrations | Embed credentials in code or committed configuration |
| **Target repositories** | Their own code, toolchain, CI, and conventions | Provide authoritative instructions or configuration to the agent |

## 5. AI-driven decisions and deterministic operations

| AI-driven (Claude Code) | Deterministic (tools and Python) |
|---|---|
| Interpreting requests and repository evidence | Calling external APIs |
| Planning and sequencing work | Parsing and validating structured data |
| Choosing which capability or tool to use | Producing reproducible outputs from the same inputs |
| Reviewing code, plans, and configuration | Enforcing explicit rules and checks |
| Summarizing and explaining results | Performing repeatable automation |

An operation belongs in deterministic code when its result must be predictable, repeatable, testable, or auditable. Judgment, interpretation, and synthesis remain with Claude Code.

## 6. Skills, subagents, and Python integration model

### 6.1 Skills

Skills are the primary mechanism for reusable capabilities.

- A skill represents one cohesive capability, such as analyzing a repository or reviewing an infrastructure plan.
- A skill defines the procedure, evaluation criteria, and expected output. It references deterministic tools instead of describing their logic in prose.
- Skills are invoked directly as slash commands. No separate commands layer is introduced by default, and logic is not duplicated between commands and skills.
- Skills that perform mutating operations make their authorization points explicit.

Another mechanism is introduced only when a concrete requirement cannot be met by skills.

### 6.2 Subagents

Subagents are optional. A subagent is introduced only when a specific task benefits from at least one of:

- **Independent context:** the task would otherwise flood or bias the main session.
- **Specialization:** the task needs a focused role or a restricted set of tools.
- **Parallel execution:** independent parts of the task can run concurrently.

The added cost in context and coordination must be justified by the concrete task.

### 6.3 Python package

Python is introduced when a concrete integration requires predictable execution. The invocation contract was decided with the first integration that needs it, remote repository analysis, and is recorded in [ADR-0007](adr/0007-python-invocation-contract.md):

- The package is invoked as a module through `uv run`, without console scripts.
- It parses arguments with the standard library.
- It writes one versioned JSON envelope to standard output.
- It uses a closed set of error codes.

Its commands, result fields, and operating values are documented in [`docs/python-package.md`](python-package.md).

Python integrations follow these principles:

- **Explicit inputs:** the target and all parameters are passed explicitly; there is no implicit state.
- **Structured output:** results are machine-readable for Claude Code, and failures are reported explicitly.
- **Non-interactive:** integrations never prompt for input.
- **Read/mutate separation:** read-only and mutating operations are clearly separated.
- **Testable in isolation:** behavior can be tested without network access or an LLM.

## 7. External integration principles

- **Reuse before building.** Prefer existing tools such as `git`, official CLIs, or existing MCP servers. A Python integration is justified only when it adds value those tools do not provide, such as aggregation, normalization, validation, idempotency, or testability.
- **Explicit boundaries.** Each integration has a clearly defined interface and does not leak provider-specific details into unrelated components.
- **No premature abstraction.** A shared provider abstraction is introduced only when at least two concrete implementations of the same kind exist.
- **Externalized credentials.** Credentials are supplied at runtime and are never stored in source code or committed configuration.

## 8. Security principles

These principles define the architectural security boundary. Operation tiers, permissions, hooks, and operational safety policies are specified in the [security policy](security-policy.md), following [ADR-0003](adr/0003-layered-security-enforcement.md).

1. **Least privilege.** Each session accesses only the target repositories it needs, and each integration uses only the permissions it requires.
2. **Tiered authorization.**
   - **Authorized workflow operations:** operations explicitly requested by the user and within the approved task scope. This includes creating branches, committing, pushing, and opening pull requests.
   - **Sensitive operations:** operations that require approval according to the security policy.
   - **Irreversible or high-impact operations:** operations that require explicit confirmation before execution, such as applying infrastructure changes, modifying cloud resources, or deleting data.
   - **Merging pull requests remains exclusively under human control.**
3. **Read/mutate separation.** Read-only and mutating operations are clearly separated in skills, tools, and Python integrations so they can be governed independently.
4. **Deterministic execution.** Repeatable operations run through deterministic tools instead of free-form generation.
5. **Untrusted external content.** Target repository content, including its instruction files, as well as issues, pull requests, logs, and tool output, is treated as data and never as instructions.
6. **Instructions are not enforcement.** Project instructions and skills describe expected behavior, but security boundaries are enforced through deterministic mechanisms.
7. **Credential safety.** Credentials are never stored in source code or committed configuration.

### 8.1 Technical verification points

The following Claude Code behaviors affect the boundary between the agent environment and target repositories. They are not assumed by this architecture and must be verified before being relied on:

| Verification point | Issue | Status |
|---|---|---|
| Which instruction files (`CLAUDE.md`) are loaded from additional or target directories | #4 | Verified for `--add-dir`: none by default; `CLAUDE.md` and `.claude/rules/` load only when `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` is set; `AGENTS.md` unverified ([environment](claude-code-environment.md#8-verified-behavior)) |
| Whether target repository `.claude/` configuration or skills can influence the agent session | #4, #5 | Verified for `--add-dir`: skills, commands, and subagents load; settings, hooks, and MCP servers do not ([security policy](security-policy.md#14-verification)); inline shell of skills is disabled ([environment](claude-code-environment.md#4-settings)) |
| How target repository instructions interact with centralized agent instructions | #4, #5 | Policy defined ([security policy](security-policy.md#7-untrusted-content-and-prompt-injection)); target instruction files are not loaded by default |
| How access to target directories is technically granted and restricted | #4, #5 | Configured: targets are attached per session with `--add-dir`, and file tools refuse reads outside working directories ([environment](claude-code-environment.md#6-working-with-target-repositories)) |
| Output handling, process lifecycle, permission behavior of subprocesses, authentication, and Git hardening for remote analysis | #14 | Verified on Windows ([environment](claude-code-environment.md#81-remote-analysis-prerequisites)). The decisions that rely on them are recorded in [ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md) and [ADR-0007](adr/0007-python-invocation-contract.md). The remaining points are listed with their owning issues in [environment Section 10](claude-code-environment.md#10-unverified-behavior). |

## 9. Extensibility principles and conventions

- **Incremental growth.** Components are added when a concrete use case requires them, not in anticipation.
- **Skills first.** New capabilities start as skills; deterministic code is added only for the parts that need it.
- **Single responsibility.** Each skill, subagent, and integration has one clearly defined purpose.
- **Minimal dependencies.** New dependencies require a concrete justification.
- **Quality by default.** Python code meets the existing quality pipeline: linting, formatting, strict type checking, security analysis, dependency auditing, and tests.
- **Cross-platform.** Capabilities work on the supported development platforms and in CI.
- **Recorded decisions.** Significant architectural decisions are documented as ADRs in [`docs/adr/`](adr/README.md).

## 10. Risks and trade-offs

| Risk | Mitigation |
|---|---|
| Dependency on Claude Code features that evolve quickly | Document roles and boundaries rather than file formats; verify behavior before relying on it |
| Non-deterministic model behavior | Enforce security boundaries deterministically, not through instructions alone |
| Prompt injection through target content or tool output | Treat all external content as untrusted data |
| Operating on the wrong target repository | Require explicit target selection in every workflow, and confirmation of proposed targets ([configuration](configuration.md#32-target-selection)) |
| Conflicting instructions or conventions from target repositories | Apply the convention precedence rules; verify loading behavior (Section 8.1) |
| Expanded access when several targets are attached | Attach only the targets required for the task |
| Duplicating functionality of existing tools in Python | Apply the reuse-before-building principle |
| Documentation drift between README, project instructions, and this document | Keep this document authoritative and link to it |
| Limited context when working with large or multiple repositories | Design skills to summarize evidence; use subagents when justified |
| Different credentials per target or provider | Keep credentials in native provider mechanisms and refer to them by credential reference ([configuration](configuration.md#6-credentials-and-secret-references)) |
| Subprocesses of approved Python processes are not evaluated by permission rules or hooks | Fully determined subprocess arguments, an environment allowlist, and a closed list of permitted commands ([security policy Section 5.7](security-policy.md#57-python-package-operations)) |
| Remote repository content persists outside the collector after analysis | Ephemeral workspaces removed in the same process; bounded evidence; residual risks RR-16 and RR-17 ([security policy Section 13](security-policy.md#13-residual-risks)) |

## 11. Related decisions and issues

Architecture decision records:

- [ADR-0001: Claude Code as the orchestration engine](adr/0001-claude-code-as-orchestration-engine.md)
- [ADR-0002: Python as the deterministic integration layer](adr/0002-python-as-deterministic-integration-layer.md)
- [ADR-0003: Layered security enforcement](adr/0003-layered-security-enforcement.md)
- [ADR-0004: Session-scoped targets and native provider configuration](adr/0004-session-scoped-targets-and-native-provider-configuration.md)
- [ADR-0005: Claude Code version support and re-verification](adr/0005-claude-code-version-support-and-re-verification.md)
- [ADR-0006: Remote repository acquisition through deterministic collectors](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md)
- [ADR-0007: Python invocation contract](adr/0007-python-invocation-contract.md)

Agent Foundation issues:

| Issue | Scope |
|---|---|
| #2 | Architecture and responsibilities (this document) |
| #3 | Project-level instructions and conventions (`CLAUDE.md`) |
| #4 | Claude Code environment configuration and structure (`.claude/`) |
| #5 | Permissions, hooks, and operational safety policies |
| #6 | Configuration model for target repositories and external providers ([configuration](configuration.md)) |
| #7 | Repository analysis skill |

Remote Repository Analysis issues:

| Issue | Scope |
|---|---|
| #14 | Verification of Claude Code and Git behavior for collector-based remote analysis |
| #15 | Remote repository acquisition and the Python invocation contract ([ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md), [ADR-0007](adr/0007-python-invocation-contract.md)) |
| #16 | GitHub repository identifier resolution (`repo resolve`) |
| #17 | Ephemeral workspace lifecycle and `workspace sweep` |
| #18 | Repository evidence collector (`repo collect`) |
| #19 | GitHub repository identifiers in the repository analysis skill |
| #20 | Local-path analysis through the evidence collector |

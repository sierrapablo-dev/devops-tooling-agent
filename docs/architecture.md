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

## 4. Components and responsibilities

| Component | Responsible for | Must not |
|---|---|---|
| **Claude Code** | Reasoning, conversation, planning, orchestration, target and tool selection, requesting approval | Bypass permissions; delegate judgment to deterministic tools, or the reverse |
| **Project instructions** | Global agent conventions and engineering standards | Contain long procedures; act as a security control |
| **Claude Code environment** | Project structure, settings, and deterministic enforcement of boundaries | Contain domain logic |
| **Skills** | Domain knowledge, workflows, and reusable procedures; invoked directly as slash commands or loaded by Claude Code | Depend on a specific target repository; perform mutations outside the authorized workflow |
| **Subagents** (optional) | Specialist roles with independent context, restricted tools, or parallel execution | Exist without a concrete justification |
| **Python package** | Predictable execution, API integrations, validation, automation, and structured data processing | Use LLM SDKs, launch Claude Code, act as an agent runtime, or require interactive input |
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

Python is introduced when a concrete integration requires predictable execution. No definitive CLI contract or CLI framework is defined in advance; the invocation mechanism is decided with the first integration that needs it, preferring the standard library.

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

These principles define the architectural security boundary. Permissions, settings, hooks, and operational safety policies are specified in issue #5.

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

| Verification point | Issue |
|---|---|
| Which instruction files (`CLAUDE.md`) are loaded from additional or target directories | #4 |
| Whether target repository `.claude/` configuration or skills can influence the agent session | #4, #5 |
| How target repository instructions interact with centralized agent instructions | #4, #5 |
| How access to target directories is technically granted and restricted | #4, #5 |

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
| Operating on the wrong target repository | Require explicit target selection in every workflow |
| Conflicting instructions or conventions from target repositories | Apply the convention precedence rules; verify loading behavior (Section 8.1) |
| Expanded access when several targets are attached | Attach only the targets required for the task |
| Duplicating functionality of existing tools in Python | Apply the reuse-before-building principle |
| Documentation drift between README, project instructions, and this document | Keep this document authoritative and link to it |
| Limited context when working with large or multiple repositories | Design skills to summarize evidence; use subagents when justified |
| Different credentials per target or provider | Define the configuration model in issue #6 |

## 11. Related decisions and issues

Architecture decision records:

- [ADR-0001: Claude Code as the orchestration engine](adr/0001-claude-code-as-orchestration-engine.md)
- [ADR-0002: Python as the deterministic integration layer](adr/0002-python-as-deterministic-integration-layer.md)

Agent Foundation issues:

| Issue | Scope |
|---|---|
| #2 | Architecture and responsibilities (this document) |
| #3 | Project-level instructions and conventions (`CLAUDE.md`) |
| #4 | Claude Code environment configuration and structure (`.claude/`) |
| #5 | Permissions, hooks, and operational safety policies |
| #6 | Configuration model for target repositories and external providers |
| #7 | Repository analysis skill |

# ADR-0001: Claude Code as the orchestration engine

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

DevOps Tooling Agent needs reasoning, a conversational interface, and orchestration of DevOps workflows across multiple external target repositories, such as application, infrastructure-as-code, and cloud infrastructure repositories.

Claude Code already provides these capabilities natively, including tool execution, permission management, context management, skills, and subagents. Reimplementing them would duplicate functionality, increase maintenance cost, and introduce a second, less capable orchestration layer.

The target repositories are owned independently and must not need to contain or install anything from this project.

## Decision

- Claude Code is the sole reasoning, conversational, and orchestration layer of the system.
- Claude Code is started from this repository, which acts as a centralized agent environment operating on explicitly selected target repositories.
- Skills are the primary mechanism for reusable capabilities and are invoked directly as slash commands. No separate commands layer is introduced by default.
- Subagents are optional and are introduced only when a specific task benefits from independent context, specialization, or parallel execution.
- The instructions and security policies that govern the agent are defined in this repository. Target repository content does not override agent security policies.

## Alternatives considered

- **Custom Python agent runtime using an LLM SDK:** duplicates Claude Code's orchestration, requires maintaining prompt, tool, and context management, and adds dependencies without a concrete benefit.
- **Python CLI that launches Claude Code as a subprocess:** inverts control, adds an unnecessary wrapper layer, and makes Claude Code a dependency of a custom runtime instead of the primary interface.
- **Installing the agent's configuration in each target repository:** couples independently owned repositories to this project and duplicates configuration across them.
- **Separate slash-command layer alongside skills:** duplicates entry points and logic, since skills can already be invoked directly.

## Consequences

- **Positive:**
  - No duplicated agent runtime or orchestration layer.
  - Native tooling, permissions, and context management are reused.
  - Capabilities are defined once and reused across all target repositories.
  - The codebase remains small and focused.
- **Negative:**
  - The project depends on Claude Code and its evolution; behavior that the architecture relies on must be verified.
  - Model behavior is partly non-deterministic, so security boundaries require deterministic enforcement rather than instructions alone.
  - Capability design must follow Claude Code conventions.
  - The interaction between the centralized environment and target repository content must be verified (see [Technical verification points](../architecture.md#81-technical-verification-points)).

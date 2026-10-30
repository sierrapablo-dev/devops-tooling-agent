# Architecture Decision Records

This directory records significant architectural decisions of DevOps Tooling Agent. The overall architecture is described in [`docs/architecture.md`](../architecture.md).

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-claude-code-as-orchestration-engine.md) | Claude Code as the orchestration engine | Accepted |
| [0002](0002-python-as-deterministic-integration-layer.md) | Python as the deterministic integration layer | Accepted |
| [0003](0003-layered-security-enforcement.md) | Layered security enforcement | Accepted |

## Conventions

- Files are named `NNNN-short-title.md`, using the next sequential four-digit number.
- Status is one of `Proposed`, `Accepted`, `Deprecated`, or `Superseded by ADR-NNNN`.
- Accepted ADRs are not rewritten. A changed decision is recorded in a new ADR that supersedes the previous one.

## Template

```markdown
# ADR-NNNN: Title

- **Status:** Proposed
- **Date:** YYYY-MM-DD

## Context

The problem, constraints, and forces that motivate the decision.

## Decision

The decision and its scope.

## Alternatives considered

- **Alternative:** Why it was not chosen.

## Consequences

- **Positive:** Benefits of the decision.
- **Negative:** Costs, limitations, and risks introduced by the decision.
```

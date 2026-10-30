# ADR-0002: Python as the deterministic integration layer

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

Some operations must not depend on LLM reasoning. API integrations, validation, structured data processing, and automation require predictable, repeatable, and testable execution.

The repository already provides a Python package with a quality pipeline that includes linting, formatting, strict type checking, security analysis, dependency auditing, and tests across supported Python versions.

Claude Code is the orchestration engine ([ADR-0001](0001-claude-code-as-orchestration-engine.md)), so the Python layer must complement it without becoming a competing runtime.

## Decision

- Python is the deterministic execution and integration layer of the system.
- Python code is executed from the agent environment, invoked by Claude Code, against explicitly specified target repositories. It is never installed in or imported by a target repository.
- Python code does not use the Anthropic SDK or any other LLM SDK, does not launch Claude Code, does not implement an agent runtime, and does not require interactive input.
- Python is introduced only when a concrete integration requires it. Existing tools are preferred when they already provide the required capability.
- No definitive CLI contract or CLI framework is defined in advance. The invocation mechanism is decided with the first integration that needs it, preferring the standard library.
- Python integrations use explicit inputs, produce structured output, report failures explicitly, separate read-only from mutating operations, and are testable without network access or an LLM.

## Alternatives considered

- **Prose instructions in skills only:** cannot guarantee predictable or repeatable results for operations that require them, and cannot be tested.
- **Ad-hoc shell scripts:** less portable across supported development platforms, harder to test, and not covered by the existing quality pipeline.
- **Python agent runtime:** duplicates Claude Code's orchestration and contradicts [ADR-0001](0001-claude-code-as-orchestration-engine.md).
- **Defining a complete CLI contract or framework now:** introduces abstractions and dependencies without a concrete integration to validate them.

## Consequences

- **Positive:**
  - Operations that require predictability are reproducible, testable, and auditable.
  - Python code benefits from the existing quality and security pipeline.
  - Claude Code can rely on structured results instead of interpreting free-form output.
  - Dependencies remain minimal until a concrete need arises.
- **Negative:**
  - The boundary between skill instructions and deterministic code must be maintained deliberately.
  - Deferring the invocation contract may require refactoring once several integrations exist.
  - Running Python from the agent environment against external targets requires explicit target handling in every integration.

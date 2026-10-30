# ADR-0004: Session-scoped targets and native provider configuration

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

The agent operates on multiple external target repositories and accesses external providers with real credentials. The architecture requires explicit target selection, treats target content as untrusted, and prohibits credentials in source code and committed configuration ([architecture Section 8](../architecture.md#8-security-principles)). The security policy deferred the configuration model for targets, provider configuration, and secret references to a dedicated design ([security policy Section 1](../security-policy.md#1-scope-and-conventions)).

At the time of this decision:

- Targets are attached per session with `--add-dir`, and target paths must not be stored in committed Claude Code configuration.
- GitHub is the only provider in use. It is accessed through the `gh` CLI, whose credentials are kept in the system keyring. An operator-level GitHub MCP plugin also exists.
- No skill, hook, or Python integration consumes project or provider configuration. The first planned skill, repository analysis (#7), needs only an explicitly selected target.
- The agent uses the operator's identity until a dedicated agent identity exists (RR-1).

## Decision

- Project configuration is session-scoped. No persistent registry of targets exists, versioned or local, and targets are not attached persistently through settings.
- Target selection is explicit. The agent uses the target the user names, or proposes a candidate from the attached directories and waits for explicit confirmation. It never selects a target automatically, even when only one is attached. Approvals apply only to the confirmed target.
- Target facts are derived from repository evidence when needed and are not stored. Each fact is reported as verified, inferred, or unknown. The hosting provider inferred from a remote URL is not reported as verified unless independently confirmed, and the default branch is reported as unknown when it cannot be determined reliably.
- Target repositories contain no agent configuration, and target content is never a configuration source for the agent.
- Provider authentication and settings remain in each provider's native mechanism at operator level. The agent environment refers to credentials only through credential references, which name a mechanism and an identifier, never a value.
- Provider integrations are chosen in the order native CLI, reviewed MCP server, Python integration. For GitHub, the `gh` CLI is the primary integration, and the operator-level MCP plugin is optional and not a dependency.
- Deployment environments are selected explicitly in each relevant operation through the provider's native mechanism. No default deployment environment exists.
- No configuration file, schema, `.mcp.json`, or configuration code is introduced. Each is introduced only with a concrete consumer, through its own issue, and meets the validation requirements in [`docs/configuration.md`](../configuration.md#8-validation-requirements).

The model is described in [`docs/configuration.md`](../configuration.md).

## Alternatives considered

- **Versioned project registry:** stores machine-specific target paths in the repository, becomes stale, and encourages implicit target selection.
- **Local, unversioned project registry:** has no consumer, cannot attach directories to a session, and would add parsing and validation code without a concrete use case.
- **Per-target convention overlays in this repository:** duplicate or override the target's own documentation and conflict with the convention precedence.
- **Agent configuration inside target repositories:** contradicts the requirement that targets contain nothing from this project, and target content is untrusted, so it could not be authoritative.
- **Shared provider abstraction or plugin registry in Python:** a premature abstraction with a single provider.
- **Loading credentials from `.env` files or an agent-managed secret store:** `.env` files are credential locations, the approach adds a dependency, and it places secrets in the session environment.
- **Credentials in a Claude Code settings `env` block:** prohibited, because the variables reach every command the session runs, including target-defined code.
- **A project `.mcp.json` for GitHub:** the `gh` CLI already provides the required capability.

## Consequences

- **Positive:**
  - No versioned or local file can select a target, a credential, or a deployment environment implicitly.
  - Project and provider configuration are separated: the link between them is derived from evidence, not stored.
  - No new dependencies, configuration formats, or code need to be maintained before a consumer exists.
  - Future integrations follow a documented procedure without coupling to a shared abstraction.
- **Negative:**
  - Targets must be attached and confirmed in every session, and target facts are re-derived each time.
  - Credential references do not provide least privilege or isolation. Native CLIs and approved target code inherit the operator's environment, and the operator's identity remains shared until the dedicated agent identity exists.
  - The first configuration consumer must define its file format, validation, and tests.

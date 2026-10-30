# ADR-0003: Layered security enforcement

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

The agent operates on external target repositories and services with real credentials. Its behavior is partly non-deterministic, and it processes untrusted content that can contain prompt injection. The architecture states that instructions are not enforcement ([Security principles](../architecture.md#8-security-principles)), but it does not define which mechanisms enforce each boundary.

Verification with the supported Claude Code version showed that:

- Claude Code permission rules hold in `auto` mode.
- Prefix-based shell rules are bypassed by wrapping the command in another shell.
- Skills, slash commands, and subagents from directories attached with `--add-dir` are loaded into the session.

The project currently has a single human maintainer, who works through an administrator identity.

## Decision

- Security is enforced in four layers: behavior (L1), Claude Code controls (L2), platform controls (L3), and human authority (L4). The normative policy is [`docs/security-policy.md`](../security-policy.md).
- Platform controls are the authoritative boundary. Every prohibited operation is prevented by a platform control, or it is recorded as a residual risk.
- Claude Code permission rules and security hooks are defense in depth. Hooks are deterministic, tested, and fail closed, and they never approve what permission rules deny or prompt for.
- Operations are classified into tiers that determine the required authorization: read-only, local workflow, external workflow, sensitive, irreversible or high-impact, and prohibited.
- The target security architecture uses a dedicated, least-privilege agent identity without administrative, ruleset, secret, or collaborator permissions. Merging pull requests remains exclusively human. Required pull request approvals are not mandated while there is a single maintainer.
- All execution of code defined by a target repository is sensitive and requires explicit approval. No safe-command allowlist is introduced until a sandbox has been implemented and validated.
- Skills, slash commands, and subagents defined by target repositories are never invoked.
- The security policy specifies the required Claude Code controls. The Claude Code configuration and the security hooks implement them, in issue #4 and a dedicated issue respectively.

## Alternatives considered

- **Instructions only:** non-deterministic, and ineffective against prompt injection.
- **Claude Code permission rules only:** prefix matching is bypassable, and rules cannot express conditions such as the working directory or the HTTP method of a request.
- **Requiring one pull request approval now:** with a single maintainer, it would block the human's own merges without adding independent review.
- **A safe-command allowlist for target toolchains:** commands such as `npm test` or `terraform plan` execute target-controlled code, so no allowlist is safe without isolation.
- **Adopting a sandbox now:** it has not been implemented or validated on the supported platforms, so it would be a speculative control.

## Consequences

- **Positive:**
  - Each boundary has a defined enforcement mechanism and an owner.
  - The Claude Code configuration can be implemented without new security decisions.
  - Residual risks are explicit, together with the work that resolves them.
- **Negative:**
  - Sensitive operations require frequent approvals until hooks and a sandbox exist.
  - Until the agent identity is migrated, the strongest controls against merging and administrative changes are behavioral and Claude Code controls only.
  - The policy depends on Claude Code behavior that must be re-verified when the supported version changes.

# ADR-0005: Claude Code version support and re-verification

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

The Claude Code controls of this repository (L2, [ADR-0003](0003-layered-security-enforcement.md)) depend on runtime behavior that was verified against one Claude Code version. ADR-0003 states that this behavior must be re-verified when the supported version changes, and the [security policy](../security-policy.md#91-settings-requirements) (C-8) requires every rule to be verified against the supported version. Neither defines which versions are supported. [Environment Section 11](../claude-code-environment.md#11-re-verification) therefore required a full re-verification of Sections 8 to 10 for every new version.

At the time of this decision:

- Sections 8 to 10 were verified with Claude Code 2.1.314, and Claude Code 2.1.316 is installed.
- Claude Code releases are numbered `major.minor.patch`. Patch releases are frequent, and the minor and major numbers change rarely.
- Patch releases do change behavior that the configuration relies on. The public changelog lists, for example, fixes to PowerShell permission checks, to `Edit` deny rules and Bash redirects, and to hook blocking on Windows in releases 2.1.309 to 2.1.316.
- A full re-verification takes a dedicated task, so requiring one for every patch release blocks all verification-dependent work.

## Decision

- **Version line.** The verified version line is the `major.minor` of the most recent full verification of [environment Sections 8 to 10](../claude-code-environment.md#8-verified-behavior). A supported version is a version of that line that is recorded as an operating version in [environment Section 11](../claude-code-environment.md#11-re-verification).
- **Minor or major change.** A version outside the verified version line requires a full re-verification of Sections 8 to 10 before the agent relies on the Claude Code controls. The re-verification establishes a new verified version line.
- **Patch change.** A new patch version of the verified version line requires a changelog review before it becomes an operating version:
  - The review covers every changelog entry after the most recent operating version, up to and including the new version.
  - Each entry that can affect a control the configuration relies on is identified: permission rules and their matching, settings and their precedence, shell and file tools, hooks, MCP, skills and subagents, instruction file loading, sandbox, and permission modes.
  - Rows of Sections 8 to 10 affected by such an entry are re-verified before the new version becomes an operating version. If no row is affected, the version becomes an operating version without re-verification.
  - The review is recorded in Section 11 with the version, the date, the changelog source, the relevant entries, and the conclusion. The human maintainer approves the conclusion in the pull request that records it.
- **Other triggers.** Changes to `.claude/settings.json`, a new shell tool, and a new MCP server still require re-verification of the affected rows, independent of the version.
- **Unsupported version.** A task that depends on verified Claude Code behavior and runs on a version that is not an operating version stops and reports the version before relying on that behavior.
- **Relationship to ADR-0003.** This decision defines the "supported version" that ADR-0003 and the security policy refer to. It does not supersede ADR-0003 and does not change any tier, rule, or control.

## Alternatives considered

- **Full re-verification for every version change:** the previous rule. It blocks work on every patch release, and the cost leads to verification being skipped rather than performed.
- **Re-verification only on major version changes:** the major version changes so rarely that this amounts to never re-verifying, and patch releases demonstrably change permission and hook behavior.
- **Accepting patch releases without review:** treats patch releases as behavior-neutral, which the changelog contradicts.
- **Pinning the Claude Code version:** delays security fixes, and the operator's installation updates independently of this repository.
- **An automated re-verification suite on every release:** the preferred long-term control, but no such suite exists. Headless verification currently needs manual fixture setup.

## Consequences

- **Positive:**
  - The agent can operate on patch releases after a short, documented review.
  - Minor and major changes keep a full re-verification.
  - Every operating version and the basis for accepting it are recorded and approved by a human.
- **Negative:**
  - Between full verifications, L2 behavior is accepted on the basis of the public changelog. Behavior changes that the changelog does not describe are not detected (RR-13).
  - The changelog review is an assessment, not a deterministic check, and depends on the completeness of the changelog and on the reviewer.
  - Each patch release still requires a small documentation change before it is an operating version.

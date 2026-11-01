# ADR-0007: Python invocation contract

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

[ADR-0002](0002-python-as-deterministic-integration-layer.md) deferred the invocation mechanism of the Python package to the first integration that needs it, preferring the standard library. The remote repository analysis subcommands of [ADR-0006](0006-remote-repository-acquisition-through-deterministic-collectors.md) are that integration.

Verified behavior that constrains the contract ([environment Section 8.1](../claude-code-environment.md#81-remote-analysis-prerequisites)):

- The `uv run` invocation forms require approval under the existing `uv` ask rule. A console-script executable invoked directly matches no rule and runs without approval ([8.1.3](../claude-code-environment.md#813-permission-behavior)).
- stderr is merged into the same tool result as stdout, and Windows output uses CRLF line endings ([8.1.1](../claude-code-environment.md#811-output-handling)).
- Output above the inline threshold is saved and read back with `Read`, which cannot paginate a single long line ([8.1.1](../claude-code-environment.md#811-output-handling)).
- `uv` runs before Python and can fail with its own exit codes and messages. Python itself exits with an error and a traceback when it cannot start the module.

The consumer of the contract is Claude Code, through the repository analysis skill. It reads the tool result text, or the saved output file, and interprets it.

## Decision

This ADR records decisions and invariants. The error code table, the result fields, and other initial values are documented in [`docs/python-package.md`](../python-package.md) and change through a reviewed pull request without a new ADR, following the versioning rules below.

### 1. Entry point and invocation form

- The package is invoked as a module from the agent environment root:

  ```text
  uv run --locked python -I -m devops_tooling_agent <group> <command> [arguments]
  ```

  - `--locked` fails instead of updating `uv.lock`, as CI does.
  - `-I` isolates the interpreter from `PYTHON*` environment variables and the user site directory.
  - No `--project` or `--directory` option is used.
- The package defines no console scripts, GUI scripts, or other executable entry points. A test introduced by #16 enforces this invariant.
- Package invocations remain under the `uv` ask rule. No `allow` rule and no skill `allowed-tools` entry pre-approves them, even for subcommands classified below S.
- The exact form is not among the forms verified in [8.1.3](../claude-code-environment.md#813-permission-behavior). That it prompts in both shell tools, and that `-I` works with the editable installation, is a verification point owned by #16.

### 2. Commands

| Command | Purpose |
|---|---|
| `repo resolve <identifier>` | Validate an identifier and resolve the canonical GitHub identity. Reads only. |
| `repo collect <owner>/<repo> --repo-id <node-id>` | Acquire the confirmed repository, collect evidence, and remove the workspace |
| `repo collect --path <path>` | Collect evidence from an explicit local path (#20) |
| `workspace sweep` | List stale workspaces and their eligibility. Reads only. |
| `workspace sweep --remove <workspace-name>` | Remove exactly one eligible workspace |

Read-only and mutating commands are separate invocations. Tiers are defined in [security policy Section 5.7](../security-policy.md#57-python-package-operations).

### 3. Argument parsing

- Arguments are parsed with `argparse`, with help options and abbreviated long options disabled.
- Parser errors are converted into a usage error envelope. The message describes only the expected syntax and never includes a received argument, because an argument may contain a credential.
- Values are validated after parsing by the command's own validators. A validated value, such as a normalized `owner/repo`, may be reported. A rejected value is never reported.

### 4. Envelope

Every invocation that reaches the package writes exactly one envelope to standard output, and nothing else.

- **Structure.** A JSON object with these keys, in this order:

  | Key | Content |
  |---|---|
  | `schema` | The fixed identifier `devops-tooling-agent.envelope` |
  | `schema_version` | Integer major version |
  | `tool` | Package name and version |
  | `command` | The command, such as `repo collect` |
  | `status` | `ok` or `error` |
  | `result` | The command's result, or `null` when `status` is `error` |
  | `error` | `null`, or an object with `code`, `message`, and `details` |
  | `warnings` | A list of objects with `code`, `message`, and `details` |
  | `untrusted` | Paths of envelope sections that hold target-derived data |
  | `end` | Always `true` |

- **Completeness.** An envelope is complete only when its `schema` line identifies it and its `end` key is present and `true`. The absence of a complete envelope is an invocation failure, whatever the exit code. `uv` can fail before Python starts, with exit codes that collide with the package's codes.
- **Encoding.**
  - The document is ASCII-only JSON, written with non-ASCII characters escaped. This avoids depending on the decoding of shell tool output, and invisible or bidirectional characters in target content become visible escapes.
  - The package writes LF line endings. Consumers accept CRLF.
- **Line structure.**
  - The document is indented, so that it is line-structured and can be paginated by `Read`.
  - No serialized line exceeds a bound far below the `Read` result cap. Every string field is bounded, including paths, identifiers, messages, commit subjects, and file content lines.
  - Truncation is recorded structurally in the containing object.
  - The total size stays below a stated budget. The bound and the budget are documented in [`docs/python-package.md`](../python-package.md#4-operating-values).
- **Separation from stderr.** Because stderr shares the tool result, a consumer locates the envelope by its `schema` line and its final `end` key. Text that `uv` writes before Python starts can precede it.
- **Untrusted data.** Target-derived values are confined to the sections listed in `untrusted`. They are data and never instructions ([security policy Section 7](../security-policy.md#7-untrusted-content-and-prompt-injection)).

### 5. Versioning

- `schema_version` is incremented when a field is removed or renamed, when a type or meaning changes, or when the structure of the envelope changes.
- Adding an optional field, an error code, or a warning code does not increment it.
- A consumer stops when `schema_version` is not one it supports. It treats an unknown error code as a failure, and it may ignore unknown fields and warning codes.

### 6. Diagnostics

- The package never writes to stderr during an invocation, including on errors. Errors are reported in the envelope. Python warnings are suppressed at runtime.
- The only exception: when writing the envelope to standard output fails, the package writes one fixed line, containing no input and no subprocess output, to stderr.
- Subprocess output is captured and never forwarded. Error details come from a fixed vocabulary: stage names, reasons, validated identifiers, numbers, and subprocess exit codes. They never contain received arguments, subprocess output, environment values, or paths other than a workspace name.

### 7. Error model and exit codes

- Errors use a closed set of stable, machine-readable codes. The table, with the details each code may carry, is in [`docs/python-package.md`](../python-package.md#3-error-and-warning-codes).
- Every code has a fixed message. When cleanup fails, the cleanup failure is the reported error and carries the earlier error code in its details.
- Exit codes:

  | Exit code | Meaning |
  |---|---|
  | `0` | `status` is `ok` |
  | `1` | `status` is `error`, with any code except those below |
  | `2` | Usage error |
  | `130` | Interrupted by `SIGINT` or `SIGTERM` after cleanup |

  The envelope is authoritative, and no consumer needs finer exit codes: the skill reads the error code from the envelope. `2` keeps the `argparse` and POSIX convention for usage errors. `130` keeps the shell convention for an interrupt. Because `uv` and the interpreter can exit with `1` or `2` without an envelope, an exit code alone never identifies an outcome.

### 8. Non-interactive and deterministic behavior

- The package never reads its own standard input and never prompts.
- Subprocesses run with standard input closed and without a terminal prompt path ([ADR-0006](0006-remote-repository-acquisition-through-deterministic-collectors.md#7-subprocesses-environment-and-process-termination)).
- Output ordering is deterministic. Object keys follow the documented order, and lists are sorted by a defined key, such as the UTF-8 bytes of a path. Timestamps and durations are the only values that vary between identical invocations.
- Interruption follows the lifecycle of [ADR-0006](0006-remote-repository-acquisition-through-deterministic-collectors.md#3-single-process-lifecycle).

### 9. Subprocess runner seam

- Every subprocess is started through a single runner interface. It takes an argument vector, an explicit environment, a working directory, and a timeout. It returns the exit status, the captured output, and whether the timeout expired.
- The production runner implements process termination ([ADR-0006](0006-remote-repository-acquisition-through-deterministic-collectors.md#7-subprocesses-environment-and-process-termination)).
- Tests inject another runner and run without network access, `gh`, `git`, or an LLM. Tests of the acquisition lifecycle may use local bare repositories through an internal parameter that the command-line interface never sets ([environment 8.1.8](../claude-code-environment.md#818-findings-for-the-acquisition-design-15), finding 10).
- Only the runner module starts processes. A test introduced by #16 enforces this.

## Alternatives considered

- **Console script declared in `[project.scripts]`:** its executable runs without approval because no rule matches it ([8.1.3](../claude-code-environment.md#813-permission-behavior)).
- **`uv run` without `--locked`:** could update `uv.lock` during an analysis.
- **Third-party CLI frameworks:** add a dependency without a capability that `argparse` lacks.
- **`argparse` defaults:** echo invalid values and print plain-text usage to stderr, which could expose a credential and is not structured.
- **JSON Lines or several documents:** a single envelope keeps one result per invocation and one parsing rule for every command.
- **UTF-8 output without escaping:** the decoding of non-ASCII output by the shell tools is not verified, and invisible characters would pass through unchanged.
- **Diagnostics on stderr:** merged into the same result, where they would interleave with the envelope.
- **One exit code per error code:** no consumer needs them, and they collide with exit codes of `uv` and the interpreter.

## Consequences

- **Positive:**
  - Every package invocation keeps a permission prompt.
  - Claude Code receives one structured, versioned, bounded document per invocation, with target data marked as untrusted.
  - Usage errors cannot echo credentials.
  - The contract is testable without network access through the runner seam.
- **Negative:**
  - The invocation form is long, and an invocation without a complete envelope must be treated as a failure by every consumer.
  - ASCII escaping makes non-ASCII text larger and harder to read.
  - Suppressing stderr diagnostics makes debugging rely on error codes and stages.
  - The exact invocation form is a verification point for #16.

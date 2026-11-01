# Python Package

This document is the amendable reference for the command-line contract and the operating values of the Python package (`src/devops_tooling_agent`). [`docs/architecture.md`](architecture.md) is the authoritative architecture description.

## 1. Scope and change procedure

The decisions and invariants are recorded in two ADRs:

- [ADR-0006](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md): remote repository acquisition through deterministic collectors.
- [ADR-0007](adr/0007-python-invocation-contract.md): the Python invocation contract.

The normative security rules for package operations are in [security policy Section 5.7](security-policy.md#57-python-package-operations).

This document records the initial values that implement those decisions:

- result fields;
- error and warning codes;
- time, size, and evidence limits;
- minimum tool versions.

Change rules:

- A value changes through a reviewed pull request, without a new ADR, as long as every invariant of ADR-0006 and ADR-0007 still holds. The pull request states which invariant each changed value satisfies.
- A change to a result field or code follows the versioning rules of [ADR-0007 Section 5](adr/0007-python-invocation-contract.md#5-versioning).
- A change that would break an invariant requires a new ADR.
- The package defines each value as a named constant. This document and the constants are kept equal, and a test introduced with the constants checks the values listed here.

The commands are not implemented yet. They are introduced by #16 (`repo resolve`), #17 (`workspace sweep` and the acquisition lifecycle), #18 (`repo collect`), and #20 (`repo collect --path`).

## 2. Commands and results

The invocation form and the command syntax are defined in [ADR-0007](adr/0007-python-invocation-contract.md#1-entry-point-and-invocation-form). Every command writes one envelope ([ADR-0007 Section 4](adr/0007-python-invocation-contract.md#4-envelope)) with `schema` set to `devops-tooling-agent.envelope` and `schema_version` set to `1`.

### 2.1 `repo resolve`

`result` fields:

| Field | Content | Source |
|---|---|---|
| `input` | The normalized, validated `owner/repo` derived from the identifier | Validation |
| `name_with_owner` | Canonical `owner/repo` | `gh repo view` |
| `node_id` | Repository node ID | `gh repo view` |
| `url` | Canonical URL | `gh repo view` |
| `visibility` | `public`, `private`, or `internal` | `gh repo view` |
| `default_branch` | Default branch name, or `null` for an empty repository | `gh repo view` |
| `viewer_permission` | Permission of the authenticated account | `gh repo view` |
| `archived` | Boolean | `gh repo view` |
| `empty` | Boolean | `gh repo view` |
| `disk_usage_kib` | Provider-reported disk usage | `gh repo view` |
| `input_matches_canonical` | Whether `input` equals `name_with_owner` exactly | Comparison |

`untrusted` is empty. Every string field either passes a strict grammar or is a fixed vocabulary value.

### 2.2 `repo collect`

`result` sections, in this order:

| Section | Content |
|---|---|
| `repository` | The fields of `repo resolve` except `input` and `input_matches_canonical`, from the resolution before acquisition |
| `acquisition` | Fetched commit, fetched reference, fetch depth, and shallow status |
| `collector` | Limits applied, truncation summary, stale workspaces detected, and cleanup status |
| `evidence` | Inventory, allowlisted file contents, sensitive file paths, agent component presence, and recent commits |

`untrusted` is `["result.evidence"]`. The field-level schema of `evidence` is defined by #18 within the invariants of [ADR-0006 Section 9](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#9-evidence-document-and-transport) and documented here when it is introduced.

### 2.3 `workspace sweep`

Without `--remove`, `result.workspaces` lists each directory with the workspace prefix. Each entry has `name`, `age_seconds`, `eligible`, and, when not eligible, `reason`. With `--remove <workspace-name>`, `result` reports the removed workspace name.

## 3. Error and warning codes

Every error has a fixed `message`. `details` contains only the keys listed here. Exit codes follow [ADR-0007 Section 7](adr/0007-python-invocation-contract.md#7-error-model-and-exit-codes): `2` for `USAGE_ERROR`, `130` for `INTERRUPTED`, and `1` for every other code.

| Code | Meaning | `details` |
|---|---|---|
| `USAGE_ERROR` | The arguments do not match the command syntax | `expected`: the syntax of the command |
| `INVALID_IDENTIFIER` | The repository identifier is not accepted | `reason`: `format`, `scheme`, `host`, `userinfo`, `port`, `query`, `fragment`, `path`, `characters`, or `length` |
| `INVALID_ARGUMENT` | Another argument is not valid | `argument`: the option name; `reason` |
| `ENVIRONMENT_ERROR` | A prerequisite is missing or unsupported: `git` or `gh` not found, a version below the minimum, or an unusable temporary directory | `reason`; `tool`; `minimum_version` |
| `AUTH_REQUIRED` | `gh` is not authenticated for `github.com` | None |
| `NOT_FOUND_OR_INACCESSIBLE` | The repository does not exist or is not accessible | None |
| `REPOSITORY_ID_MISMATCH` | The node ID or canonical name differs from the confirmed identity | `stage`: `before_acquisition` or `after_acquisition` |
| `UNSUPPORTED_REPOSITORY` | The repository cannot be acquired within scope | `reason`: `empty`, `default_branch_name`, or `object_check_failed` |
| `LIMIT_EXCEEDED` | A hard limit makes the operation impossible | `limit`; `maximum`; `observed` |
| `TIMEOUT` | The internal deadline expired | `stage` |
| `PROVIDER_ERROR` | GitHub, the network, or TLS failed for a reason other than authentication or access | `stage`; `exit_code` |
| `UNSAFE_ENVIRONMENT` | The configuration isolation check failed | `check`: `configuration_scope` or `url_rewrite` |
| `CLEANUP_FAILED` | The workspace could not be removed | `workspace`: the workspace name; `prior_error`: the earlier error code or `null` |
| `WORKSPACE_NOT_ELIGIBLE` | The workspace named for removal is not eligible | `reason`: `not_found`, `name`, `link`, `location`, `marker`, or `age` |
| `INTERRUPTED` | `SIGINT` or `SIGTERM` was received and cleanup ran | `stage` |
| `INTERNAL_ERROR` | An unexpected error | None |

Warning codes:

| Code | Meaning | `details` |
|---|---|---|
| `STALE_WORKSPACES_DETECTED` | Workspaces of other invocations exist under the system temporary directory | `count`; `names` |

## 4. Operating values

Each value is an initial value. The invariant it must satisfy is stated with it.

### 4.1 Time

| Constant | Value | Invariant and rationale |
|---|---|---|
| `GH_CALL_TIMEOUT_SECONDS` | `30` | Below `RESOLVE_DEADLINE_SECONDS` |
| `RESOLVE_DEADLINE_SECONDS` | `60` | Below the default shell tool timeout of 120 seconds ([environment 8.1.2](claude-code-environment.md#812-process-lifecycle)), so `repo resolve` needs no `timeout` parameter |
| `COLLECT_TOOL_TIMEOUT_MS` | `300000` | The tool timeout that the skill requests for `repo collect`. At or below the documented maximum of 600,000 ms. |
| `COLLECT_DEADLINE_SECONDS` | `240` | Internal deadline of `repo collect`, from start to emission. Below `COLLECT_TOOL_TIMEOUT_MS`, which leaves 60 seconds for `uv` startup and environment synchronization. |
| `CLEANUP_RESERVE_SECONDS` | `30` | Acquisition and collection stop when less time remains. Greater than the removal retry budget plus process termination. |
| `REMOVAL_ATTEMPTS` | `20` | Removal retry budget of 10 seconds. A 3-second lock was released within 10 × 0.5 seconds ([environment 8.1.7](claude-code-environment.md#817-cleanup-on-windows)). |
| `REMOVAL_RETRY_DELAY_SECONDS` | `0.5` | As verified in [environment 8.1.7](claude-code-environment.md#817-cleanup-on-windows) |
| `SWEEP_MIN_AGE_SECONDS` | `3600` | More than ten times the maximum collector runtime (`COLLECT_DEADLINE_SECONDS`) |

### 4.2 Acquisition

| Constant | Value | Invariant and rationale |
|---|---|---|
| `MAX_REPO_DISK_USAGE_KIB` | `1048576` | 1 GiB, checked against the provider-reported disk usage before any workspace is created. GitHub recommends repositories below this size. |
| `MAX_WORKSPACE_BYTES` | `536870912` | 512 MiB, enforced during the fetch. A shallow fetch is normally far smaller than the full repository. |
| `WORKSPACE_POLL_SECONDS` | `1` | Interval of the workspace size check during the fetch |
| `FETCH_DEPTH` | `20` | Equal to the number of recent commits that the skill reports |

### 4.3 Evidence

| Constant | Value | Invariant and rationale |
|---|---|---|
| `RECENT_COMMITS` | `20` | At most `FETCH_DEPTH`. Matches the recent activity window of the local-path workflow. |
| `MAX_TREE_ENTRIES` | `200000` | Tree entries examined. Beyond it, the inventory and counts are reported as truncated. |
| `MAX_INVENTORY_PATHS` | `1000` | Paths listed individually. Counts by extension cover every examined entry. |
| `MAX_CONTENT_FILES` | `50` | Allowlisted files whose content is included |
| `MAX_FILE_BYTES` | `16384` | Content included per file |
| `MAX_FILE_LINES` | `300` | Lines included per file |
| `MAX_TOTAL_CONTENT_CHARS` | `100000` | Serialized content of all files. Below `MAX_ENVELOPE_CHARS`. |
| `MAX_BLOB_BYTES` | `1048576` | Blobs above this size are not read |
| `BINARY_SNIFF_BYTES` | `8000` | A NUL byte in this prefix marks a file as binary, as Git does |
| `MAX_COMMIT_SUBJECT_CHARS` | `200` | Commit subject length before serialization |

### 4.4 Output

| Constant | Value | Invariant and rationale |
|---|---|---|
| `MAX_LINE_CHARS` | `512` | Maximum length of every serialized line, excluding the line ending, so every string field is bounded. Far below the `Read` cap of 25,000 tokens per result ([environment 8.1.1](claude-code-environment.md#811-output-handling)). |
| `MAX_ENVELOPE_CHARS` | `200000` | Total envelope budget, which keeps the evidence to a fraction of the session context. Above the inline threshold of 30,000 characters, so collection results are normally read back with `Read`. |

### 4.5 Workspaces

| Value | Initial value |
|---|---|
| Workspace name | `devops-tooling-agent-` followed by the random suffix of the standard library's temporary directory function |
| Marker file | `.devops-tooling-agent-workspace.json` in the workspace root |
| Marker content | `marker` (`devops-tooling-agent-workspace`), `version` (`1`), and `created_at` (UTC, ISO 8601) |

### 4.6 Tool versions

| Tool | Minimum | Rationale |
|---|---|---|
| Git | 2.32 | `GIT_CONFIG_GLOBAL`, which configuration isolation requires |
| `gh` | 2.40.0 | Multi-account semantics of `gh auth status` |

Verification used Git 2.49.0 and `gh` 2.92.0 ([environment 8.1](claude-code-environment.md#81-remote-analysis-prerequisites)).

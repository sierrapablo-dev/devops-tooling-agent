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

`repo resolve` is implemented (#16). The other commands are introduced by #17 (`workspace sweep` and the acquisition lifecycle), #18 (`repo collect`), and #20 (`repo collect --path`).

## 2. Commands and results

The invocation form and the command syntax are defined in [ADR-0007](adr/0007-python-invocation-contract.md#1-entry-point-and-invocation-form). Every command writes one envelope ([ADR-0007 Section 4](adr/0007-python-invocation-contract.md#4-envelope)) with `schema` set to `devops-tooling-agent.envelope` and `schema_version` set to `1`.

Envelope fields common to every command:

| Field | Content |
|---|---|
| `tool` | An object with `name` (`devops-tooling-agent`) and `version` (the package version) |
| `command` | The command, such as `repo resolve`. For a `USAGE_ERROR`, the command when the parser identified it, otherwise `null`. It is always a fixed value, never a received argument. |
| `warnings` | Empty for `repo resolve` |

A serialized envelope that would exceed `MAX_LINE_CHARS` on any line or `MAX_ENVELOPE_CHARS` in total is a defect, and is replaced by an `INTERNAL_ERROR` envelope. No `repo resolve` field can be truncated, because every emitted value is bounded by validation.

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

#### Identifier validation

Validation is offline and ASCII-only, and happens before any subprocess runs. Accepted forms:

| Form | `.git` suffix |
|---|---|
| `https://github.com/<owner>/<repo>` | One optional `.git` suffix is removed |
| `git@github.com:<owner>/<repo>` | One optional `.git` suffix is removed |
| `<owner>/<repo>` | Not removed. It is part of the repository name, so `o/r.git` is looked up as `r.git`. If GitHub resolves it to another name, `input_matches_canonical` is `false`, and the selection flow stops and asks. |

Rules:

- **Owner:** letters, digits, and single hyphens, without a leading or trailing hyphen, at most `MAX_OWNER_CHARS` characters (GitHub's account name rules). Legacy accounts that do not follow the current rules are rejected.
- **Repository:** letters, digits, `.`, `_`, and `-`, excluding `.` and `..`, at most `MAX_REPO_NAME_CHARS` characters.
- **Identifier:** at most `MAX_IDENTIFIER_CHARS` characters. The longest valid identifier has 163.
- **Scheme and host:** accepted only as lowercase `https` and `github.com`. `HTTPS://` is rejected as `scheme`, and `GitHub.com` as `host`. No normalization step exists.
- **Trailing slash:** rejected as `path`, like any other empty or extra segment.
- **SCP form:** the user must be exactly `git`. Any other user is rejected as `userinfo`.

Each rejection reports exactly one reason. The first rule that fails, in this order, gives the reason:

1. `length`: the identifier exceeds `MAX_IDENTIFIER_CHARS`.
2. `format`: the identifier is empty.
3. `characters`: a character other than printable ASCII, including whitespace, control characters, non-ASCII characters, `%`, and `\`.
4. Structure, by form:
   - With `://`: `scheme` (not exactly `https`), then `userinfo` (`@` in the authority), `port` (`:` in the authority), `host` (not exactly `github.com`), `query` or `fragment` (whichever of `?` and `#` comes first), and `path` (not exactly two segments).
   - With `@` and no `://`: `userinfo` (user other than `git`, or `@` in the host), `format` (no `:`), `host`, `query` or `fragment`, then `format` (one segment) or `path` (more than two segments).
   - Otherwise: `query` or `fragment`, `format` (contains `:`, or one segment), then `path` (more than two segments).
5. Components, owner first, then repository: `characters`, then `length`, then `format`.

A rejected identifier is never echoed in any output.

#### `gh` commands

`gh` is resolved once to an absolute path before any subprocess runs. Resolution walks the allowlisted `PATH`, skips empty, relative, and current-directory entries, and on Windows accepts only `.EXE` files, in the order given by `PATHEXT`. Batch files are refused because they run through `cmd.exe`, an interpreter wrapper ([security policy Section 5.7.3](security-policy.md#573-subprocess-rules)). The working directory of every `gh` call is the directory of the resolved executable.

| Stage | Command | Outcome |
|---|---|---|
| `gh_version` | `gh --version` | A first line that is not `gh version <major>.<minor>.<patch>`, or a non-zero exit, is `ENVIRONMENT_ERROR` with `version_unknown`. A version below `GH_MIN_VERSION` is `version_unsupported`. |
| `gh_auth_status` | `gh auth status --hostname github.com` | Only the exit status is used. Any non-zero status is `AUTH_REQUIRED`. |
| `gh_repo_view` | `gh repo view github.com/<owner>/<repo> --json defaultBranchRef,diskUsage,id,isArchived,isEmpty,nameWithOwner,url,viewerPermission,visibility` | Exit 4 is `AUTH_REQUIRED`. A non-zero exit whose captured stderr contains `Could not resolve to a Repository with the name` is `NOT_FOUND_OR_INACCESSIBLE`. Any other non-zero exit is `PROVIDER_ERROR`. |

The whole invocation is bounded by `RESOLVE_DEADLINE_SECONDS`. Each call's timeout is the smaller of `GH_CALL_TIMEOUT_SECONDS` and the remaining deadline. Expiry is `TIMEOUT` with the stage of the call.

Known limitation: `gh auth status` exits with `1` both without authentication and when the network fails ([environment Section 8.1.9](claude-code-environment.md#819-package-invocation-and-resolution-outcomes-16)). A network failure during that stage is therefore reported as `AUTH_REQUIRED`. Its output is not parsed ([ADR-0006 Section 4](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#4-authentication)).

#### Provider values

The output of `gh repo view` must be a JSON object with exactly the requested fields. A truncated, non-JSON, or inconsistent output is `PROVIDER_ERROR` with `stage: gh_repo_view` and `exit_code: 0`.

| Field | Rule |
|---|---|
| `name_with_owner` | Passes the owner and repository grammar above |
| `url` | Equals `https://github.com/` followed by `name_with_owner` |
| `node_id` | `R_` followed by 1 to 96 characters of `[A-Za-z0-9_-]`, or the legacy form `MDEwOlJlcG9zaXRvcnk` followed by 1 to 96 characters of `[A-Za-z0-9+/]` and up to two `=` |
| `visibility` | `PUBLIC`, `PRIVATE`, and `INTERNAL`, mapped to lowercase |
| `viewer_permission` | `ADMIN`, `MAINTAIN`, `WRITE`, `TRIAGE`, and `READ`, mapped to lowercase. A missing or empty value is `null`. |
| `archived`, `empty` | JSON booleans |
| `disk_usage_kib` | An integer from 0 to 2^63 − 1 |
| `default_branch` | See the matrix below |

A branch name is accepted only when it matches a strict subset of Git branch names:

- at most `MAX_BRANCH_NAME_CHARS` characters;
- components of letters, digits, `.`, `_`, and `-`, separated by single `/`;
- no component starts with `.` or ends with `.lock`;
- the name does not start with `-`, contain `..`, or end with `.`, and is not `HEAD`.

Default branch matrix (`isEmpty` against the reported `defaultBranchRef`):

| `isEmpty` | Branch reported | Outcome |
|---|---|---|
| `false` | A name that matches the grammar | `default_branch` is the name |
| `false` | A name that does not match the grammar | `UNSUPPORTED_REPOSITORY` with `reason: default_branch_name` |
| `false` | None (`null` or an empty name) | `PROVIDER_ERROR` |
| `true` | None (`null` or an empty name) | `default_branch` is `null` |
| `true` | Any name | `PROVIDER_ERROR` |

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
| `ENVIRONMENT_ERROR` | A prerequisite is missing or unsupported: `git` or `gh` not found, a version below the minimum, or an unusable temporary directory | `reason`: `not_found`, `version_unsupported`, or `version_unknown`; `tool`: `gh` or `git`; `minimum_version`: the documented minimum version of `tool` |
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

The package defines a code when a command that emits it is implemented. `repo resolve` emits `USAGE_ERROR`, `INVALID_IDENTIFIER`, `ENVIRONMENT_ERROR`, `AUTH_REQUIRED`, `NOT_FOUND_OR_INACCESSIBLE`, `UNSUPPORTED_REPOSITORY` (only `default_branch_name`), `TIMEOUT`, `PROVIDER_ERROR`, `INTERRUPTED`, and `INTERNAL_ERROR`. #17 adds the `ENVIRONMENT_ERROR` reason for an unusable temporary directory.

Fixed values:

- `USAGE_ERROR` `expected` is always `repo resolve <identifier>` until other commands are implemented.
- `stage` values of `repo resolve`:
  - `TIMEOUT` and `PROVIDER_ERROR`: `gh_version`, `gh_auth_status`, or `gh_repo_view`.
  - `INTERRUPTED`: those three, plus `validation` (arguments and identifier), `environment` (environment and executable resolution), and `output` (after the command finished, before the envelope was written).
- `repo resolve` has no workspace. On `INTERRUPTED`, its cleanup is the termination of the running `gh` child.
- `PROVIDER_ERROR` with `exit_code` `0` means that `gh` succeeded but its output did not satisfy the provider value rules.

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

| Constant | Value | Rationale |
|---|---|---|
| `GIT_MIN_VERSION` | `2.32.0` | Git 2.32, which introduced `GIT_CONFIG_GLOBAL`, required by configuration isolation |
| `GH_MIN_VERSION` | `2.40.0` | Multi-account semantics of `gh auth status` |

Verification used Git 2.49.0 and `gh` 2.92.0 ([environment 8.1](claude-code-environment.md#81-remote-analysis-prerequisites)).

### 4.7 Validation

| Constant | Value | Invariant and rationale |
|---|---|---|
| `MAX_IDENTIFIER_CHARS` | `256` | Above the longest valid identifier (163 characters). Checked first, so that no other rule processes an unbounded input. |
| `MAX_OWNER_CHARS` | `39` | GitHub's maximum account name length |
| `MAX_REPO_NAME_CHARS` | `100` | GitHub's maximum repository name length |
| `MAX_BRANCH_NAME_CHARS` | `255` | Keeps the `default_branch` line below `MAX_LINE_CHARS` |

### 4.8 Subprocesses

| Constant | Value | Invariant and rationale |
|---|---|---|
| `GH_MAX_OUTPUT_BYTES` | `65536` | Captured bytes per stream of each `gh` call. The resolution output is below 1 KB ([environment 8.1.9](claude-code-environment.md#819-package-invocation-and-resolution-outcomes-16)). Output above the bound is discarded and the result is marked as truncated. |

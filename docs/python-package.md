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

`repo resolve` (#16), `workspace sweep`, the internal acquisition lifecycle (#17), and `repo collect` (#18) are implemented. `repo collect --path` is introduced by #20.

## 2. Commands and results

The invocation form and the command syntax are defined in [ADR-0007](adr/0007-python-invocation-contract.md#1-entry-point-and-invocation-form). Every command writes one envelope ([ADR-0007 Section 4](adr/0007-python-invocation-contract.md#4-envelope)) with `schema` set to `devops-tooling-agent.envelope` and `schema_version` set to `1`.

Envelope fields common to every command:

| Field | Content |
|---|---|
| `tool` | An object with `name` (`devops-tooling-agent`) and `version` (the package version) |
| `command` | The command, such as `repo resolve`. For a `USAGE_ERROR`, the command when the parser identified it, otherwise `null`. It is always a fixed value, never a received argument. |
| `warnings` | Empty for `repo resolve` and `workspace sweep`. `repo collect` reports its warnings in success and error envelopes alike. |

A serialized envelope that would exceed `MAX_LINE_CHARS` on any line or `MAX_ENVELOPE_CHARS` in total is a defect, and is replaced by an `INTERNAL_ERROR` envelope. No `repo resolve` field can be truncated, because every emitted value is bounded by validation. `repo collect` holds both bounds by construction ([Section 2.2](#22-repo-collect)): every string is fitted to its serialized line, and the evidence is reduced to the budget before the envelope is written.

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

`repo collect <owner>/<repo> --repo-id <node-id>` acquires the confirmed repository, collects bounded evidence, removes the workspace, and then writes one envelope. Its tier is S ([security policy Section 5.7.1](security-policy.md#571-tiers)).

#### Arguments

- `<owner>/<repo>` is validated with the identifier rules of [Section 2.1](#identifier-validation), in their order of reasons. Only the canonical form is accepted: an identifier that `repo resolve` accepts but that is not exactly the normalized `<owner>/<repo>`, such as a URL, is rejected as `INVALID_IDENTIFIER` with `format`.
- `--repo-id` is required and must match the node ID grammar of [Section 2.1](#provider-values). Otherwise the result is `INVALID_ARGUMENT` with `argument` `--repo-id` and `reason` `format`.
- A rejected value is never reported.

#### Lifecycle

The lifecycle follows [ADR-0006 Section 3](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#3-single-process-lifecycle):

1. **`validation`**: the arguments, offline.
2. **`environment`**: the subprocess environment, and the resolution of `gh` and then `git` ([Section 2.4](#24-acquisition-lifecycle-internal)).
3. **Resolution**:
   - `gh --version`, `gh auth status`, and `gh repo view`, as in [Section 2.1](#gh-commands).
   - The reported node ID and canonical name are compared with the arguments, exactly and case-sensitively. A difference is `REPOSITORY_ID_MISMATCH` with `before_acquisition`.
   - No workspace and no Git command exist at that point.
4. **`stale_detection`**: workspaces of other invocations are reported with `STALE_WORKSPACES_DETECTED`. They are never removed.
5. **Acquisition**: the lifecycle of [Section 2.4](#24-acquisition-lifecycle-internal).
6. **Second binding check**, inside the acquisition lifecycle:
   - `gh repo view` runs again.
   - A difference is `REPOSITORY_ID_MISMATCH` with `after_acquisition`. No evidence is collected, and the workspace is removed.
7. **Collection**: evidence collection, then a final deadline check (stage `collection`).
8. **Removal and output**: the workspace is removed. Only then is the result assembled, fitted to the budget, and written.

Every `gh` and Git call shares the `COLLECT_DEADLINE_SECONDS` deadline:

- Each call's timeout is the smaller of its per-call bound (`GH_CALL_TIMEOUT_SECONDS` or `GIT_CALL_TIMEOUT_SECONDS`) and the working time. The working time is the remaining time minus `CLEANUP_RESERVE_SECONDS`.
- Expiry is `TIMEOUT` with the stage of the call. Partial evidence is never returned.

#### Git read commands

Collection uses only the read commands of [security policy Section 5.7.3](security-policy.md#573-subprocess-rules), rule 8, through the acquisition handle. Every vector starts with the fixed options of rule 9 and `--git-dir=<repository>`. The remaining arguments follow these rules:

- Every argument is a constant or an object ID that matches `[0-9a-f]{40}` or `[0-9a-f]{64}`.
- `<commit>` is the fetched commit of the handle, never `HEAD`.
- `<blob>` comes from the tree listing.
- No path controlled by the target is ever an argument.

| Stage | Command | Capture |
|---|---|---|
| `git_rev_parse` | `rev-parse --is-shallow-repository` | `GIT_MAX_OUTPUT_BYTES` |
| `git_ls_tree` | `ls-tree -r -z --long --full-tree --end-of-options <commit>` | `MAX_TREE_OUTPUT_BYTES` |
| `git_cat_file` | `cat-file --end-of-options blob <blob>`, once per read candidate | `MAX_FILE_BYTES`, which is at least `BINARY_SNIFF_BYTES` |
| `git_log` | `log --no-show-signature --no-mailmap --no-decorate --no-color --max-count=<RECENT_COMMITS> --format=%H%x00%cI%x00%s%x00 --end-of-options <commit>` | `GIT_MAX_OUTPUT_BYTES` |

- `--no-mailmap` is required: in a bare repository, `mailmap.blob` defaults to `HEAD:.mailmap`, which is target content.
- No option runs a diff, textconv, or filter driver. `cat-file` never uses `--textconv` or `--filters`.
- A blob whose listed size exceeds `MAX_BLOB_BYTES` is never read.
- Every option is available in Git 2.32 (`GIT_MIN_VERSION`).
- A non-zero exit of any of these commands is `INTERNAL_ERROR`, because the fetched objects already passed the object and connectivity checks.

The tree mode and object type give the entry type. Symbolic links and `other` entries are never read, and link targets are never resolved.

| Mode and object type | Entry type |
|---|---|
| `100644` or `100755` with `blob` | `file` |
| `120000` with `blob` | `symlink` |
| `160000` with `commit` | `submodule` |
| Any other combination, such as `040000` or `100664` | `other` |

#### Classification

The package is the source of truth for the pattern lists below ([ADR-0006 Section 11](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#11-evidence-allowlist-and-collector-sources)). Tests keep the lists equal to the constants of `classify.py`, and keep the sensitive file names and excluded directories equal to [`inspection-guide.md`](../.claude/skills/analyze-repository/inspection-guide.md).

Patterns are matched against the components of a Git path, which are bytes separated by `/`:

- `*` matches any sequence of bytes within one component. No other character is special.
- A pattern without `/` matches any single component.
- A pattern with `/` matches consecutive components at any depth. A trailing `/` only documents that the pattern names a directory.
- A pattern that starts with `/` is anchored: it matches the whole path. Only the allowlist uses anchored patterns.
- Matching is case-insensitive for ASCII letters only, on the raw bytes.
- For sensitive files and agent components, each component also matches with its trailing ASCII dots and spaces removed. This fails toward classifying an entry as not readable.

Each entry gets exactly one class. The precedence is sensitive, agent component, excluded directory, then regular:

- Within a class, the shallowest matching component decides.
- When that component is the last one, the entry itself belongs to the class.
- Otherwise, the directory up to that component belongs to the class, and every entry below it inherits the class.
- An excluded directory name never applies to the entry itself.

As a consequence, a file below `.claude/`, `.github/instructions/`, or `.cursor/rules/` is never read.

#### Evidence patterns

| Class | Patterns |
|---|---|
| Sensitive files | `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `*.tfstate`, `*.tfstate.backup`, `.terraform/`, `.npmrc`, `.pypirc`, `.netrc`, `_netrc`, `.git-credentials`, `.dockercfg`, `.docker/config.json`, `.ssh/`, `.aws/`, `.kube/`, `kubeconfig`, `*.kubeconfig`, `*.jks`, `*.keystore`, `*.ppk`, `*.ovpn`, `credentials`, `credentials.json`, `service-account*.json`, `*.tfvars`, `*.tfvars.json`, `Pulumi.*.yaml`, `secrets.*`, `*.secrets`, `*vault*.yml`, `*vault*.yaml`, `*.sops.*` |
| Agent components | `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md`, `GEMINI.md`, `.cursorrules`, `.windsurfrules`, `.clinerules`, `.mcp.json`, `.github/copilot-instructions.md`, `.claude/`, `.cursor/rules/`, `.github/instructions/` |
| Excluded directories | `.git`, `node_modules`, `bower_components`, `vendor`, `.venv`, `venv`, `__pycache__`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `dist`, `build`, `out`, `target`, `bin`, `obj`, `.gradle`, `.next`, `.nuxt`, `coverage`, `.terraform`, `.terragrunt-cache`, `.serverless`, `.cache` |

A regular entry of type `file` is a content candidate when it matches the allowlist:

- Anchored patterns match the whole path. The other patterns match the file name.
- The first matching category, in the order of this table, gives the category.

| Category | Patterns |
|---|---|
| `ci` | `/.github/workflows/*.yml`, `/.github/workflows/*.yaml`, `/.gitlab-ci.yml`, `/azure-pipelines.yml`, `/.circleci/config.yml`, `/bitbucket-pipelines.yml`, `/.buildkite/*.yml`, `/.buildkite/*.yaml`, `/.github/dependabot.yml`, `/.github/dependabot.yaml`, `Jenkinsfile`, `cloudbuild.yaml`, `cloudbuild.yml`, `buildspec.yml`, `renovate.json`, `renovate.json5` |
| `manifest` | `package.json`, `pnpm-workspace.yaml`, `lerna.json`, `nx.json`, `turbo.json`, `pyproject.toml`, `setup.cfg`, `requirements*.txt`, `Pipfile`, `go.mod`, `go.work`, `Cargo.toml`, `rust-toolchain.toml`, `pom.xml`, `build.gradle`, `build.gradle.kts`, `settings.gradle`, `settings.gradle.kts`, `*.csproj`, `*.sln`, `global.json`, `Gemfile`, `composer.json`, `mix.exs`, `.nvmrc`, `.node-version`, `.python-version`, `.tool-versions`, `Makefile`, `justfile`, `Taskfile.yml`, `tox.ini`, `noxfile.py` |
| `infrastructure` | `*.tf`, `*.tftest.hcl`, `terragrunt.hcl`, `Pulumi.yaml`, `cdk.json`, `*.bicep`, `Chart.yaml`, `kustomization.yaml`, `kustomization.yml`, `serverless.yml`, `fly.toml`, `vercel.json`, `netlify.toml`, `app.yaml`, `Procfile` |
| `container` | `Dockerfile`, `Dockerfile.*`, `*.Dockerfile`, `Containerfile`, `compose.yml`, `compose.yaml`, `docker-compose*.yml`, `docker-compose*.yaml`, `.dockerignore` |
| `quality` | `.editorconfig`, `.pre-commit-config.yaml`, `.prettierrc*`, `prettier.config.*`, `.eslintrc*`, `eslint.config.*`, `tsconfig.json`, `ruff.toml`, `.ruff.toml`, `mypy.ini`, `.flake8`, `pytest.ini`, `.coveragerc`, `codecov.yml`, `jest.config.*`, `vitest.config.*`, `playwright.config.*`, `cypress.config.*`, `phpunit.xml`, `.golangci.yml`, `.golangci.yaml`, `rustfmt.toml`, `.rustfmt.toml`, `commitlint.config.*`, `.commitlintrc*`, `release-please-config.json`, `.releaserc*`, `.gitleaks.toml` |
| `documentation` | `README*`, `CONTRIBUTING*`, `SECURITY*`, `CODEOWNERS`, `/.github/pull_request_template.md` |

Lockfiles, `.gitmodules`, licenses, changelogs, and codes of conduct are deliberately not candidates. They appear in the inventory only.

#### Content selection

Candidates are selected by category round-robin:

- Each category is ordered by literal path depth (the number of `/`), then by the UTF-8 bytes of the path. Root-anchored patterns keep their literal depth.
- Selection runs in rounds. Each round takes the next candidate of every category, in category order, until `MAX_CONTENT_FILES` blobs have been read or every candidate has been taken.
- A candidate whose listed size exceeds `MAX_BLOB_BYTES` is skipped without being read and without consuming a read. The next candidate of the same category takes its turn.
- Every other selected candidate is read once and consumes a read, including a binary one. A file with a NUL byte in its first `BINARY_SNIFF_BYTES` bytes is binary and is never included.
- Once `MAX_TOTAL_CONTENT_CHARS` is exhausted, the remaining candidates are not read.
- Included files appear in selection order, which is also the priority order of the budget reduction.

Content handling:

- At most `MAX_FILE_BYTES` bytes are used, and a UTF-8 BOM is removed.
- Decoding is tolerant UTF-8. A replacement is recorded in `decoding_replaced`. An incomplete sequence at a byte cut is dropped without being recorded.
- Lines are split at LF, and a CR before the LF is removed. A lone CR stays in its line.
- At most `MAX_FILE_LINES` lines are included.
- A line that would exceed `MAX_LINE_CHARS` when serialized is cut, and its index is recorded in `truncated_lines`.
- `MAX_TOTAL_CONTENT_CHARS` counts the serialized content lines of every file, including indentation, quotes, escapes, separators, and line endings.

#### Result

`result` sections, in this order:

| Section | Content |
|---|---|
| `repository` | The fields of `repo resolve` except `input` and `input_matches_canonical`, from the resolution before acquisition |
| `acquisition` | `commit` (the fetched commit object ID), `reference` (`refs/heads/<default branch>`), `depth` (`FETCH_DEPTH`), and `shallow` (Boolean, from `rev-parse --is-shallow-repository`) |
| `collector` | See below |
| `evidence` | See below |

`collector` keys, in order:

- `limits`: each limit name and its value;
- `limits_reached`: the names of the limits reached anywhere in the evidence;
- `reductions`: the sections reduced by the budget, in reduction order;
- `stale_workspaces`: the number of stale workspaces detected;
- `cleanup`: `removed`. The random workspace name is not reported on success, so identical invocations produce identical output.

`untrusted` is `["result.evidence"]`:

- Outside `result.evidence`, the result holds only fixed vocabulary, values that pass a strict grammar, and numbers.
- Error details and warnings never contain target-derived values.
- Every list of limit names follows the order of `collector.limits`.

Common structures:

- **Path list**, with keys in this order:
  - `total`: the number of paths;
  - `listed`: the number of paths in `paths`;
  - `truncated`: whether `listed` is below `total`;
  - `truncation`: the limits that shortened the list (`MAX_INVENTORY_PATHS`, `MAX_ENVELOPE_CHARS`);
  - `paths`: sorted by the bytes of the original Git path, at most `MAX_INVENTORY_PATHS`;
  - `sizes` (inventory files only): the blob sizes, in the order of `paths`;
  - `truncated_path_indexes`: the indexes of paths cut to fit their line;
  - `replaced_path_indexes`: the indexes of paths whose decoding replaced bytes.
- **Directory list**: `total`, `listed`, `truncated`, `truncation`, and `directories`. Each directory has `path`, `path_truncated`, `path_replaced`, and `entries`, which is the number of examined entries below it.

`evidence` sections, in this order:

| Section | Keys, in order |
|---|---|
| `inventory` | `examined_entries`, `unparsed_entries`, `tree_truncated`, `tree_truncation` (`null`, `MAX_TREE_ENTRIES`, or `MAX_TREE_OUTPUT_BYTES`), `counts`, `extensions`, `files` (path list with `sizes`), `symlinks` (path list), `submodules` (path list), and `excluded_directories` (directory list) |
| `sensitive_files` | `classification_complete`, `classification_truncation`, `files` (path list), and `directories` (directory list) |
| `agent_components` | The same keys as `sensitive_files`. Directories are reported by path and entry count only, and the paths inside them are never listed. |
| `contents` | `candidates`, `read`, `included`, `total_chars`, `truncated`, `truncation`, `files`, and `not_included` |
| `git` | `requested` (`RECENT_COMMITS`), `returned`, `unparsed`, `truncated`, `truncation`, and `recent_commits` |

Section details:

- **`counts`**:
  - `files`, `symlinks`, `submodules`, and `other` count the regular entries by type.
  - `sensitive`, `agent_components`, and `excluded` count the entries of those classes.
  - Together, they cover every parsed entry.
- **`extensions`**:
  - `counts` maps an extension to its number of regular files outside excluded directories, sensitive files, and agent components.
  - The extension is the lowercase suffix from the last dot of the file name. It is counted under its own key only when it matches `\.[a-z0-9_+-]{1,MAX_EXTENSION_CHARS}`.
  - Keys are ordered by count, highest first, then by bytes. At most `MAX_EXTENSIONS` keys are listed.
  - `none` counts the files without an extension, including names whose only dot is their first character.
  - `other` counts the files with a nonconforming extension or with an extension beyond `MAX_EXTENSIONS`.
  - `truncated` and `truncation` follow.
- **`classification_complete`**:
  - It is `false` when the tree listing was truncated or a record could not be parsed. Sensitive files and agent components are then classified only among the examined entries.
  - `classification_truncation` repeats `tree_truncation`.
- **`contents`**:
  - `candidates` counts the allowlisted files, `read` counts the blobs read, and `included` counts the files in `files`.
  - `total_chars` is the sum of the `content_chars` of the included files.
  - `truncation` holds the limits that left candidates out: `MAX_BLOB_BYTES`, `MAX_CONTENT_FILES`, `MAX_TOTAL_CONTENT_CHARS`, and `MAX_ENVELOPE_CHARS`.
  - Each file has these keys, in order: `path`, `path_truncated`, `path_replaced`, `category`, `size`, `decoding_replaced`, `truncated`, `truncation` (`MAX_FILE_BYTES`, `MAX_FILE_LINES`, `MAX_LINE_CHARS`, `MAX_TOTAL_CONTENT_CHARS`), `truncated_lines`, `content_chars`, and `content`, which is an array of lines.
  - `not_included` has a path list for each reason, in this order: `binary`, `MAX_BLOB_BYTES`, `MAX_CONTENT_FILES`, `MAX_TOTAL_CONTENT_CHARS`, and `MAX_ENVELOPE_CHARS`. The last list counts the files removed by the budget without listing their paths.
- **`recent_commits`**:
  - Each commit has `commit`, `date`, `subject`, `subject_truncated`, and `subject_replaced`. `date` is `%cI`, or `null` when it does not match the strict ISO 8601 form.
  - Subjects are cut to `MAX_COMMIT_SUBJECT_CHARS`, then to their line.
  - Author and committer names and emails are never collected.
  - When the `log` capture reaches `GIT_MAX_OUTPUT_BYTES`, a partial last record with a valid commit and date is kept with `subject_truncated`, and `truncation` includes `GIT_MAX_OUTPUT_BYTES`.
  - `RECENT_COMMITS` appears in `truncation` when the list reached that number, because older history may exist.
  - Git-side truncation with `%<(N,trunc)` is not used. Its `..` marker and its padding cannot be told apart from subject text, and it counts display columns, not characters.

#### Budget reduction

When the whole envelope, warnings included, would exceed `MAX_ENVELOPE_CHARS`, sections are reduced in this fixed order until it fits:

1. `inventory.files`
2. `inventory.symlinks`
3. `inventory.submodules`
4. `inventory.excluded_directories`
5. `contents.not_included.binary`
6. `contents.not_included.MAX_BLOB_BYTES`
7. `contents.not_included.MAX_CONTENT_FILES`
8. `contents.not_included.MAX_TOTAL_CONTENT_CHARS`
9. `contents.files`, from the end of the selection order
10. `git.recent_commits`
11. `inventory.extensions`, from the least frequent
12. `agent_components.files`
13. `agent_components.directories`
14. `sensitive_files.files`
15. `sensitive_files.directories`

Rules:

- Each step keeps the longest prefix of its list that fits, measured on the real serialization. A list that cannot fit even when empty is emptied, and the next step runs.
- A reduced container gets `truncated` set and `MAX_ENVELOPE_CHARS` added to its `truncation`. The step's name is added to `collector.reductions`, and `MAX_ENVELOPE_CHARS` to `collector.limits_reached`.
- `repository`, `acquisition`, `collector`, and every count are never reduced. Their worst case is bounded by grammars and constants, and a test shows that it fits a fraction of the budget. The `INTERNAL_ERROR` replacement therefore remains a defect path only.

### 2.3 `workspace sweep`

`workspace sweep` starts no process. Listing is S and removal is X ([security policy Section 5.7.1](security-policy.md#571-tiers)).

Without `--remove`, `result` fields:

| Field | Content |
|---|---|
| `workspaces` | Each directory, link, or junction directly under the system temporary directory whose name starts with `WORKSPACE_PREFIX`, sorted by the UTF-8 bytes of the name, at most `MAX_SWEEP_ENTRIES` |
| `total` | The number of such entries found |
| `truncated` | Whether `total` exceeds the number listed |

Each entry of `workspaces`, with keys in this order:

| Key | Content |
|---|---|
| `name` | The workspace name, or `null` when it does not match the workspace name pattern. Such a name is never reported. |
| `age_seconds` | Whole seconds since the marker's `created_at`, negative for a future time, or `null` when no valid marker was read |
| `eligible` | Boolean |
| `reason` | `null` when eligible, otherwise a `WORKSPACE_NOT_ELIGIBLE` reason |

With `--remove <workspace-name>`, `result` is `{"removed": "<workspace-name>"}`. The name is validated before any filesystem access, and an ineligible workspace is refused with `WORKSPACE_NOT_ELIGIBLE` and never removed. Eligibility is evaluated again before each removal attempt. Removal uses the procedure of [Section 2.4](#24-acquisition-lifecycle-internal). If it fails, the result is `CLEANUP_FAILED` with `prior_error` set to `null`. An interruption during removal is reported as `INTERRUPTED` with `stage: removal`, and the marker, which is removed last, keeps a partially removed workspace listed.

Eligibility ([security policy Section 5.7.5](security-policy.md#575-workspaces)) is evaluated in this order, and the first failing check gives the reason:

1. `name`: the name does not match `WORKSPACE_PREFIX` followed by exactly `WORKSPACE_SUFFIX_CHARS` lowercase hexadecimal digits.
2. `not_found`: no entry has that name, or the entry is not a directory.
3. `link`: the entry is a symbolic link, a junction, or any other reparse point.
4. `location`: the resolved entry is not directly under the resolved system temporary directory, or it is on another device (a mount point on POSIX).
5. `marker`: the marker is missing, is not a regular file, is a link, exceeds `MARKER_MAX_BYTES`, or does not have exactly the documented content ([Section 4.5](#45-workspaces)), including a malformed `created_at`.
6. `age`: the age from the marker is not above `SWEEP_MIN_AGE_SECONDS`, including a `created_at` in the future.

### 2.4 Acquisition lifecycle (internal)

The acquisition lifecycle is an internal context manager (`acquire.Acquisition`) for `repo collect` (#18). It is not exposed as a command. The caller orchestrates the steps of [ADR-0006 Section 3](adr/0006-remote-repository-acquisition-through-deterministic-collectors.md#3-single-process-lifecycle) that are not part of it:

- before entering: argument validation, environment construction, executable resolution (`git` is resolved like `gh`), resolution and binding, and stale workspace detection (`workspace.detect_stale`, reported as `STALE_WORKSPACES_DETECTED`);
- inside the `with` block: the binding check after the fetch and evidence collection, through the yielded handle's read commands and shared deadline.

Entering, in this order:

1. Refuse an empty repository (`UNSUPPORTED_REPOSITORY` with `empty`), a default branch outside the grammar of [Section 2.1](#provider-values), and a provider disk usage above `MAX_REPO_DISK_USAGE_KIB` (`LIMIT_EXCEEDED`). Build the credential helper value. Nothing is on disk yet.
2. Resolve the system temporary directory and create the workspace ([Section 4.5](#45-workspaces)).
3. Run `git version` and check `GIT_MIN_VERSION`.
4. Run the isolation self-check.
5. Initialize the bare repository, fetch the default branch, and resolve the fetched commit.

The handle reports the workspace name, the repository location, the fetched reference, the commit, the fetch depth, and the deadline. Leaving, including on any error or interruption, removes the workspace. The cleanup outcome is available afterwards. A cleanup failure raises `CLEANUP_FAILED`, which takes precedence over any earlier error and carries its code as `prior_error`.

**System temporary directory.** The candidates are those of the standard library's `tempfile`, in its order: `TMPDIR`, `TEMP`, and `TMP` of the package's environment, then `%USERPROFILE%\AppData\Local\Temp`, `%SYSTEMROOT%\Temp`, `c:\temp`, and `c:\tmp` on Windows, or `/tmp`, `/var/tmp`, and `/usr/tmp` on POSIX. The first candidate that is absolute, resolves through `realpath` to an existing directory, and is writable is used. Unlike `tempfile`, nothing is written to test a candidate, relative and drive-relative candidates are skipped, and the current directory is never used. A resolved directory equal to or inside the agent environment (the working directory of the invocation) is refused. Every failure is `ENVIRONMENT_ERROR` with `temporary_directory`.

**Git commands.** Every argument vector is built by one function, which permits only the commands of [security policy Section 5.7.3](security-policy.md#573-subprocess-rules), rule 8, and always passes the options of rule 9. The working directory of every Git command is the workspace root, which is outside the repository data. Each Git command runs with the allowlisted environment plus the fixed Git values of [Section 5.7.4](security-policy.md#574-subprocess-environment), and `GIT_CONFIG_GLOBAL` names the workspace's empty configuration file.

| Stage | Command | Outcome |
|---|---|---|
| `git_version` | `git version` | A first line that is not `git version <major>.<minor>.<patch>`, or a non-zero exit, is `ENVIRONMENT_ERROR` with `version_unknown`. A version below `GIT_MIN_VERSION` is `version_unsupported`. |
| `isolation_check` | `git --git-dir=<repository> config --list --show-scope`, before the repository exists | The output must equal exactly one `command` line per rule 9 option, in order. Entries injected through `GIT_CONFIG_COUNT` or `GIT_CONFIG_PARAMETERS` are also reported with the `command` scope, so any extra, missing, or other-scope line fails. A non-zero exit or a truncated output also fails. Failure is `UNSAFE_ENVIRONMENT` with `configuration_scope`. |
| `isolation_check` | `git --git-dir=<repository> ls-remote --get-url <url>` | The output must equal the canonical URL. Otherwise, `UNSAFE_ENVIRONMENT` with `url_rewrite`. No network access. |
| `git_init` | `git init --bare --template= --quiet <repository>` | A non-zero exit is `INTERNAL_ERROR`. |
| `git_fetch` | `git -c credential.helper= -c credential.helper=<helper> … --git-dir=<repository> fetch --no-tags --no-recurse-submodules --no-auto-maintenance --no-write-fetch-head --depth=<FETCH_DEPTH> --quiet https://github.com/<owner>/<repo> +refs/heads/<branch>:refs/heads/<branch>` | A captured stderr containing `fsck error` is `UNSUPPORTED_REPOSITORY` with `object_check_failed`. Any other non-zero exit is `PROVIDER_ERROR`. A workspace above `MAX_WORKSPACE_BYTES`, measured every `WORKSPACE_POLL_SECONDS` during the fetch and once after it, terminates the fetch and is `LIMIT_EXCEEDED`. |
| `git_rev_parse` | `git --git-dir=<repository> rev-parse --verify --quiet --end-of-options refs/heads/<branch>^{commit}` | An output that is not one object ID, or a non-zero exit, is `PROVIDER_ERROR`. |

The credential helper value is `!'<gh>' auth git-credential`, where `<gh>` is the resolved absolute path of `gh` with forward slashes. Git runs a helper that starts with `!` through its own shell. A `gh` path that contains a single quote or a control character is refused with `ENVIRONMENT_ERROR` and `unsupported_path`, and it is never escaped.

**Time.** The caller starts the deadline (`COLLECT_DEADLINE_SECONDS` for `repo collect`). Each Git command runs only while the working time, which is the remaining time minus `CLEANUP_RESERVE_SECONDS`, is positive. The fetch timeout is the working time. Every other Git command is also bounded by `GIT_CALL_TIMEOUT_SECONDS`. Expiry is `TIMEOUT` with the stage of the command.

**Termination and removal.** Every subprocess run ends by terminating the child's process tree and confirming that no descendant remains within `TERMINATION_WAIT_SECONDS`: the Job Object on Windows, and the session's process group on POSIX ([environment Section 8.1.10](claude-code-environment.md#8110-workspace-lifecycle-and-acquisition-17)). If that cannot be confirmed, the workspace is not removed, and the result is `CLEANUP_FAILED`. Removal:

- re-validates the workspace (name, real directory, location, and marker) before each attempt;
- removes links and junctions as links and never follows them;
- makes read-only files and directories writable before retrying their removal;
- removes the marker last, then the directory;
- makes at most `REMOVAL_ATTEMPTS` attempts, `REMOVAL_RETRY_DELAY_SECONDS` apart.

`SIGINT` and `SIGTERM` are recorded, not raised, while the workspace is created and while it is removed. A signal received during removal, after an otherwise successful lifecycle, is reported as `INTERRUPTED` with `stage: cleanup` once the removal has finished.

**Known limitation.** Git for Windows limits paths to 260 characters unless `core.longpaths` is set, which [security policy Section 5.7.3](security-policy.md#573-subprocess-rules), rule 9, does not include. A system temporary directory above about 125 characters, or a very long default branch name, makes the fetch fail as `PROVIDER_ERROR` ([environment Section 8.1.10](claude-code-environment.md#8110-workspace-lifecycle-and-acquisition-17)).

**Test seam.** An internal `source_url` parameter replaces the canonical URL with a `file://` URL of a local bare repository and adds `-c protocol.file.allow=always`. The command-line interface never sets it ([ADR-0007 Section 9](adr/0007-python-invocation-contract.md#9-subprocess-runner-seam)).

## 3. Error and warning codes

Every error has a fixed `message`. `details` contains only the keys listed here. Exit codes follow [ADR-0007 Section 7](adr/0007-python-invocation-contract.md#7-error-model-and-exit-codes): `2` for `USAGE_ERROR`, `130` for `INTERRUPTED`, and `1` for every other code.

| Code | Meaning | `details` |
|---|---|---|
| `USAGE_ERROR` | The arguments do not match the command syntax | `expected`: the syntax of the command |
| `INVALID_IDENTIFIER` | The repository identifier is not accepted | `reason`: `format`, `scheme`, `host`, `userinfo`, `port`, `query`, `fragment`, `path`, `characters`, or `length` |
| `INVALID_ARGUMENT` | Another argument is not valid | `argument`: the option name; `reason`: `format` |
| `ENVIRONMENT_ERROR` | A prerequisite is missing or unsupported: `git` or `gh` not found, a version below the minimum, a `gh` path that cannot be quoted for the credential helper, or an unusable temporary directory | `reason`: `not_found`, `version_unsupported`, `version_unknown`, `unsupported_path`, or `temporary_directory`; `tool`: `gh`, `git`, or `null` for `temporary_directory`; `minimum_version`: the documented minimum version of `tool`, or `null` for `temporary_directory` |
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

The package defines a code when a command or internal lifecycle that emits it is implemented.

| Emitter | Codes |
|---|---|
| `repo resolve` | `USAGE_ERROR`, `INVALID_IDENTIFIER`, `ENVIRONMENT_ERROR` (`not_found`, `version_unsupported`, `version_unknown` for `gh`), `AUTH_REQUIRED`, `NOT_FOUND_OR_INACCESSIBLE`, `UNSUPPORTED_REPOSITORY` (only `default_branch_name`), `TIMEOUT`, `PROVIDER_ERROR`, `INTERRUPTED`, and `INTERNAL_ERROR` |
| `workspace sweep` | `USAGE_ERROR`, `ENVIRONMENT_ERROR` (only `temporary_directory`), `WORKSPACE_NOT_ELIGIBLE`, `CLEANUP_FAILED` (with `prior_error` `null`), `INTERRUPTED`, and `INTERNAL_ERROR` |
| `repo collect` | `USAGE_ERROR`, `INVALID_IDENTIFIER`, `INVALID_ARGUMENT`, `ENVIRONMENT_ERROR` (`not_found`, `version_unsupported`, and `version_unknown` for `gh` and `git`; `unsupported_path` for `gh`; `temporary_directory`), `AUTH_REQUIRED`, `NOT_FOUND_OR_INACCESSIBLE`, `REPOSITORY_ID_MISMATCH`, `UNSUPPORTED_REPOSITORY` (`empty`, `default_branch_name`, `object_check_failed`), `LIMIT_EXCEEDED`, `TIMEOUT`, `PROVIDER_ERROR`, `UNSAFE_ENVIRONMENT`, `CLEANUP_FAILED`, `INTERRUPTED`, and `INTERNAL_ERROR` (also for a failed Git read command after the fetch). Warning: `STALE_WORKSPACES_DETECTED`. |
| Acquisition lifecycle and Git resolution (for `repo collect`) | `ENVIRONMENT_ERROR` (`not_found`, `version_unknown`, and `version_unsupported` for `git`; `unsupported_path` for `gh`; `temporary_directory`), `UNSUPPORTED_REPOSITORY` (`empty`, `default_branch_name`, `object_check_failed`), `LIMIT_EXCEEDED`, `TIMEOUT`, `PROVIDER_ERROR`, `UNSAFE_ENVIRONMENT`, `CLEANUP_FAILED`, `INTERRUPTED`, and `INTERNAL_ERROR` |

Fixed values:

- `USAGE_ERROR` `expected` is the syntax of the command that the parser identified from the first two arguments: `repo resolve <identifier>`, `repo collect <owner>/<repo> --repo-id <node-id>`, or `workspace sweep [--remove <workspace-name>]`. When no command is identified, it is all three, in this order, separated by ` | `.
- `stage` values of `repo resolve`:
  - `TIMEOUT` and `PROVIDER_ERROR`: `gh_version`, `gh_auth_status`, or `gh_repo_view`.
  - `INTERRUPTED`: those three, plus `validation` (arguments and identifier), `environment` (environment and executable resolution), and `output` (after the command finished, before the envelope was written).
- `repo resolve` has no workspace. On `INTERRUPTED`, its cleanup is the termination of the running `gh` child.
- `stage` values of `workspace sweep` for `INTERRUPTED`: `validation`, `environment` (temporary directory resolution), `listing`, `removal`, and `output`.
- `stage` values of the acquisition lifecycle:
  - `TIMEOUT`: `workspace`, `git_version`, `isolation_check`, `git_init`, `git_fetch`, `git_rev_parse`, and `git_` followed by the read command with `-` replaced by `_` (such as `git_ls_tree`).
  - `PROVIDER_ERROR`: `git_fetch` or `git_rev_parse`.
  - `INTERRUPTED`: those, plus `validation` and `cleanup`.
- `stage` values of `repo collect`:
  - `TIMEOUT`: `gh_version`, `gh_auth_status`, `gh_repo_view`, the acquisition stages above, `git_rev_parse`, `git_ls_tree`, `git_cat_file`, `git_log`, and `collection` (the deadline check after collection).
  - `PROVIDER_ERROR`: `gh_repo_view`, `git_fetch`, or `git_rev_parse`.
  - `INTERRUPTED`: those, plus `validation`, `environment`, `stale_detection`, `cleanup`, and `output`.
  - `REPOSITORY_ID_MISMATCH`: `before_acquisition` or `after_acquisition`.
- `INVALID_ARGUMENT` `argument` is `--repo-id`, and `reason` is `format`.
- `LIMIT_EXCEEDED` `limit` is the name of the constant: `MAX_REPO_DISK_USAGE_KIB` (`maximum` and `observed` in KiB) or `MAX_WORKSPACE_BYTES` (in bytes).
- `CLEANUP_FAILED` `prior_error` is the code of the error that was being reported when cleanup failed, `INTERRUPTED` for a signal, `INTERNAL_ERROR` for an unexpected error, or `null`. When termination could not be confirmed after a timeout or an exceeded workspace size, it is `TIMEOUT` or `LIMIT_EXCEEDED`.
- `PROVIDER_ERROR` with `exit_code` `0` means that `gh` succeeded but its output did not satisfy the provider value rules.

Warning codes:

| Code | Meaning | `details` |
|---|---|---|
| `STALE_WORKSPACES_DETECTED` | Workspaces of other invocations exist under the system temporary directory | `count`: the number found; `names`: their names, sorted by UTF-8 bytes, at most `MAX_SWEEP_ENTRIES` |

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
| `TERMINATION_WAIT_SECONDS` | `10` | Bound on confirming that no descendant remains after a run. `CLEANUP_RESERVE_SECONDS` exceeds `REMOVAL_ATTEMPTS` × `REMOVAL_RETRY_DELAY_SECONDS` plus this value. Termination took less than 0.1 seconds in verification ([environment 8.1.10](claude-code-environment.md#8110-workspace-lifecycle-and-acquisition-17)). |
| `GIT_CALL_TIMEOUT_SECONDS` | `30` | Timeout of every Git command except the fetch, which uses the working time. Below `COLLECT_DEADLINE_SECONDS` minus `CLEANUP_RESERVE_SECONDS`. |
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
| `MAX_TREE_ENTRIES` | `200000` | Tree entries examined. Beyond it, the inventory, the counts, and the classification of sensitive files and agent components are reported as covering only the examined entries. |
| `MAX_INVENTORY_PATHS` | `1000` | Paths or directories listed individually in each path list. Counts cover every examined entry; counts by extension cover the examined regular files outside excluded directories, sensitive files, and agent components. |
| `MAX_CONTENT_FILES` | `50` | Allowlisted blobs read for content. A binary candidate consumes a read; a blob above `MAX_BLOB_BYTES` does not, because it is never read. |
| `MAX_FILE_BYTES` | `16384` | Content included per file |
| `MAX_FILE_LINES` | `300` | Lines included per file |
| `MAX_TOTAL_CONTENT_CHARS` | `100000` | Serialized content lines of all files, including indentation, quotes, escapes, separators, and line endings. Below `MAX_ENVELOPE_CHARS`. |
| `MAX_BLOB_BYTES` | `1048576` | Blobs above this size, from the tree listing, are not read |
| `BINARY_SNIFF_BYTES` | `8000` | A NUL byte in this prefix marks a file as binary, as Git does. At most `MAX_FILE_BYTES`, so one bounded read serves both. |
| `MAX_COMMIT_SUBJECT_CHARS` | `200` | Commit subject length before serialization |
| `MAX_EXTENSIONS` | `200` | Extension keys in the counts by extension, most frequent first. The others are counted together, so the counts stay a small fraction of `MAX_ENVELOPE_CHARS`. |
| `MAX_EXTENSION_CHARS` | `16` | Longest extension counted under its own key, after the dot. Longer or nonconforming extensions are counted together, so a key always fits a line. |

### 4.4 Output

| Constant | Value | Invariant and rationale |
|---|---|---|
| `MAX_LINE_CHARS` | `512` | Maximum length of every serialized line, excluding the line ending, so every string field is bounded. Far below the `Read` cap of 25,000 tokens per result ([environment 8.1.1](claude-code-environment.md#811-output-handling)). |
| `MAX_ENVELOPE_CHARS` | `200000` | Total envelope budget, which keeps the evidence to a fraction of the session context. Above the inline threshold of 30,000 characters, so collection results are normally read back with `Read`. |

### 4.5 Workspaces

| Constant | Value | Invariant and rationale |
|---|---|---|
| `WORKSPACE_PREFIX` | `devops-tooling-agent-` | Identifies workspaces of this package in the system temporary directory |
| `WORKSPACE_SUFFIX_CHARS` | `32` | Lowercase hexadecimal digits from `secrets.token_hex`, 128 random bits. The name has a fixed grammar that is validated before any filesystem access, which the undocumented suffix of `tempfile.mkdtemp` would not provide. |
| `MARKER_FILE_NAME` | `.devops-tooling-agent-workspace.json` | In the workspace root. Created exclusively before any Git command, and removed last. |
| `MARKER_ID` | `devops-tooling-agent-workspace` | The `marker` value |
| `MARKER_VERSION` | `1` | The `version` value |
| `MARKER_MAX_BYTES` | `1024` | A larger marker is invalid. The marker written by the package has less than 120 bytes. |
| `GLOBAL_CONFIG_FILE_NAME` | `gitconfig` | The empty Git global configuration file in the workspace root, named by `GIT_CONFIG_GLOBAL` |
| `REPOSITORY_DIR_NAME` | `repository.git` | The bare repository in the workspace root. Git runs with the workspace root as its working directory, outside the repository data. |
| `MAX_SWEEP_ENTRIES` | `200` | Entries listed by `workspace sweep`. A full listing stays below `MAX_ENVELOPE_CHARS`. |

The workspace is created with an exclusive `os.mkdir` and mode `0o700`. On Windows, Python 3.12.4 and later apply that mode as an access control list that grants access only to the current user, which is why the package requires Python 3.12.4 or later. The marker is a single JSON line with the keys `marker`, `version`, and `created_at`, in this order. `created_at` is the UTC creation time in the form `YYYY-MM-DDTHH:MM:SSZ`.

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
| `GIT_MAX_OUTPUT_BYTES` | `65536` | Captured bytes per stream of each Git command of the acquisition lifecycle, and of `rev-parse` and `log` during collection. The isolation self-check prints less than 300 bytes. A truncated self-check output fails the check. |
| `MAX_TREE_OUTPUT_BYTES` | `64000000` | Captured bytes per stream of the tree listing (`ls-tree`). Equal to `MAX_TREE_ENTRIES` × 320: a long record with a SHA-256 object ID has 101 fixed bytes, plus 219 bytes of average path. A truncated listing is reported, never parsed past its last complete record. |

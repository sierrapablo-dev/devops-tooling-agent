"""What is collected: evidence patterns and entry classification (ADR-0006 Section 11).

The package is the source of truth for evidence file patterns, sensitive file
names, excluded directories, and agent component names. ``inspection-guide.md``
remains the source of truth for how evidence is interpreted, and a test keeps
its sensitive file names and excluded directories equal to the lists here.

Pattern syntax, matched against the components of a Git path (bytes separated
by ``/``):

- ``*`` matches any sequence of bytes within one component. No other character
  is special.
- A pattern without ``/`` matches any single component.
- A pattern with ``/`` matches consecutive components at any depth. A trailing
  ``/`` only documents that the pattern names a directory.
- A pattern that starts with ``/`` is anchored: it matches the whole path from
  the root. Only the allowlist uses anchored patterns.

Matching is case-insensitive for ASCII letters only. For sensitive files and
agent components, each component is also matched with its trailing ASCII dots
and spaces removed, which fails toward classifying an entry as not readable.

Precedence: sensitive, then agent component, then excluded directory, then the
allowlist. Within a class, the shallowest matching component decides. When it
is the last component, the entry itself belongs to the class; otherwise the
directory up to that component does, and every entry below it inherits the
class.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

# inspection-guide.md, "Sensitive files", in table order.
SENSITIVE_PATTERNS: Final[tuple[str, ...]] = (
    # Credential locations (security policy Section 8.3)
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "*.tfstate",
    "*.tfstate.backup",
    ".terraform/",
    # Other credential and key material
    ".npmrc",
    ".pypirc",
    ".netrc",
    "_netrc",
    ".git-credentials",
    ".dockercfg",
    ".docker/config.json",
    ".ssh/",
    ".aws/",
    ".kube/",
    "kubeconfig",
    "*.kubeconfig",
    "*.jks",
    "*.keystore",
    "*.ppk",
    "*.ovpn",
    "credentials",
    "credentials.json",
    "service-account*.json",
    # Configuration that commonly holds secret values
    "*.tfvars",
    "*.tfvars.json",
    "Pulumi.*.yaml",
    "secrets.*",
    "*.secrets",
    "*vault*.yml",
    "*vault*.yaml",
    "*.sops.*",
)

# inspection-guide.md, "Excluded directories", in its order.
EXCLUDED_DIRECTORIES: Final[tuple[str, ...]] = (
    ".git",
    "node_modules",
    "bower_components",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "out",
    "target",
    "bin",
    "obj",
    ".gradle",
    ".next",
    ".nuxt",
    "coverage",
    ".terraform",
    ".terragrunt-cache",
    ".serverless",
    ".cache",
)

# The union of issue #18 and inspection-guide.md, "Agent instruction files".
# Reported by presence only: files by path, directories by path and entry count.
AGENT_COMPONENT_PATTERNS: Final[tuple[str, ...]] = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    "AGENTS.md",
    "GEMINI.md",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".mcp.json",
    ".github/copilot-instructions.md",
    ".claude/",
    ".cursor/rules/",
    ".github/instructions/",
)

# Allowlisted evidence files whose content is read, by category, in priority
# order. Lockfiles, .gitmodules, licenses, changelogs, and codes of conduct are
# deliberately absent: they appear in the inventory only.
ALLOWLIST: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "ci",
        (
            "/.github/workflows/*.yml",
            "/.github/workflows/*.yaml",
            "/.gitlab-ci.yml",
            "/azure-pipelines.yml",
            "/.circleci/config.yml",
            "/bitbucket-pipelines.yml",
            "/.buildkite/*.yml",
            "/.buildkite/*.yaml",
            "/.github/dependabot.yml",
            "/.github/dependabot.yaml",
            "Jenkinsfile",
            "cloudbuild.yaml",
            "cloudbuild.yml",
            "buildspec.yml",
            "renovate.json",
            "renovate.json5",
        ),
    ),
    (
        "manifest",
        (
            "package.json",
            "pnpm-workspace.yaml",
            "lerna.json",
            "nx.json",
            "turbo.json",
            "pyproject.toml",
            "setup.cfg",
            "requirements*.txt",
            "Pipfile",
            "go.mod",
            "go.work",
            "Cargo.toml",
            "rust-toolchain.toml",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
            "*.csproj",
            "*.sln",
            "global.json",
            "Gemfile",
            "composer.json",
            "mix.exs",
            ".nvmrc",
            ".node-version",
            ".python-version",
            ".tool-versions",
            "Makefile",
            "justfile",
            "Taskfile.yml",
            "tox.ini",
            "noxfile.py",
        ),
    ),
    (
        "infrastructure",
        (
            "*.tf",
            "*.tftest.hcl",
            "terragrunt.hcl",
            "Pulumi.yaml",
            "cdk.json",
            "*.bicep",
            "Chart.yaml",
            "kustomization.yaml",
            "kustomization.yml",
            "serverless.yml",
            "fly.toml",
            "vercel.json",
            "netlify.toml",
            "app.yaml",
            "Procfile",
        ),
    ),
    (
        "container",
        (
            "Dockerfile",
            "Dockerfile.*",
            "*.Dockerfile",
            "Containerfile",
            "compose.yml",
            "compose.yaml",
            "docker-compose*.yml",
            "docker-compose*.yaml",
            ".dockerignore",
        ),
    ),
    (
        "quality",
        (
            ".editorconfig",
            ".pre-commit-config.yaml",
            ".prettierrc*",
            "prettier.config.*",
            ".eslintrc*",
            "eslint.config.*",
            "tsconfig.json",
            "ruff.toml",
            ".ruff.toml",
            "mypy.ini",
            ".flake8",
            "pytest.ini",
            ".coveragerc",
            "codecov.yml",
            "jest.config.*",
            "vitest.config.*",
            "playwright.config.*",
            "cypress.config.*",
            "phpunit.xml",
            ".golangci.yml",
            ".golangci.yaml",
            "rustfmt.toml",
            ".rustfmt.toml",
            "commitlint.config.*",
            ".commitlintrc*",
            "release-please-config.json",
            ".releaserc*",
            ".gitleaks.toml",
        ),
    ),
    (
        "documentation",
        (
            "README*",
            "CONTRIBUTING*",
            "SECURITY*",
            "CODEOWNERS",
            "/.github/pull_request_template.md",
        ),
    ),
)

CATEGORIES: Final[tuple[str, ...]] = tuple(category for category, _ in ALLOWLIST)

EntryClass = Literal["sensitive", "agent", "excluded", "regular"]

_TRAILING: Final = b". "


Components = tuple[re.Pattern[bytes], ...]


@dataclass(frozen=True)
class PatternSet:
    """Compiled patterns. Single-component patterns share one expression."""

    single: re.Pattern[bytes] | None
    multi: tuple[Components, ...]
    anchored: tuple[Components, ...]


def _source(glob: str) -> bytes:
    return b".*".join(re.escape(part.encode("ascii")) for part in glob.split("*"))


def _regex(source: bytes) -> re.Pattern[bytes]:
    return re.compile(source, re.IGNORECASE | re.DOTALL)


def compile_patterns(patterns: Sequence[str]) -> PatternSet:
    singles: list[bytes] = []
    multi: list[Components] = []
    anchored: list[Components] = []
    for pattern in patterns:
        body = pattern.strip("/")
        if not body or "//" in body or not pattern.isascii():
            raise ValueError("invalid pattern")
        parts = body.split("/")
        if pattern.startswith("/"):
            anchored.append(tuple(_regex(_source(part)) for part in parts))
        elif len(parts) == 1:
            singles.append(_source(parts[0]))
        else:
            multi.append(tuple(_regex(_source(part)) for part in parts))
    single = _regex(b"|".join(b"(?:" + source + b")" for source in singles)) if singles else None
    return PatternSet(single, tuple(multi), tuple(anchored))


def _variants(component: bytes, tolerant: bool) -> tuple[bytes, ...]:
    if not tolerant:
        return (component,)
    stripped = component.rstrip(_TRAILING)
    if stripped and stripped != component:
        return (component, stripped)
    return (component,)


def _matches(regex: re.Pattern[bytes], component: bytes, tolerant: bool) -> bool:
    return any(regex.fullmatch(variant) for variant in _variants(component, tolerant))


def _window(regexes: Components, components: Sequence[bytes], tolerant: bool) -> bool:
    return all(
        _matches(regex, part, tolerant) for regex, part in zip(regexes, components, strict=True)
    )


def match_end(patterns: PatternSet, components: Sequence[bytes], *, tolerant: bool) -> int | None:
    """The smallest index of a component that ends a match of any pattern."""
    best: int | None = None
    if patterns.single is not None:
        for index, component in enumerate(components):
            if _matches(patterns.single, component, tolerant):
                best = index
                break
    for regexes in patterns.multi:
        size = len(regexes)
        limit = len(components) if best is None else best
        for end in range(size - 1, limit):
            if _window(regexes, components[end - size + 1 : end + 1], tolerant):
                best = end
                break
    for regexes in patterns.anchored:
        size = len(regexes)
        if size == len(components) and _window(regexes, components, tolerant):
            best = size - 1 if best is None else min(best, size - 1)
    return best


_SENSITIVE: Final = compile_patterns(SENSITIVE_PATTERNS)
_AGENT: Final = compile_patterns(AGENT_COMPONENT_PATTERNS)
_EXCLUDED: Final = compile_patterns(EXCLUDED_DIRECTORIES)
_ALLOWLIST: Final = tuple(
    (category, compile_patterns(patterns)) for category, patterns in ALLOWLIST
)


@dataclass(frozen=True)
class Classification:
    """The class of one entry.

    ``directory`` is the path of the directory that gives the class, or
    ``None`` when the entry itself matched. ``category`` is the allowlist
    category of a regular entry, or ``None``.
    """

    entry_class: EntryClass
    directory: bytes | None = None
    category: str | None = None


def split(path: bytes) -> list[bytes]:
    return path.split(b"/")


def classify(path: bytes) -> Classification:
    """Classify a Git path. Never reads content."""
    components = split(path)
    last = len(components) - 1

    def directory(end: int) -> bytes | None:
        return None if end == last else b"/".join(components[: end + 1])

    end = match_end(_SENSITIVE, components, tolerant=True)
    if end is not None:
        return Classification("sensitive", directory(end))
    end = match_end(_AGENT, components, tolerant=True)
    if end is not None:
        return Classification("agent", directory(end))
    # An excluded name applies to directories only, never to the entry itself.
    end = match_end(_EXCLUDED, components[:last], tolerant=False)
    if end is not None:
        return Classification("excluded", b"/".join(components[: end + 1]))
    for category, patterns in _ALLOWLIST:
        if (
            patterns.anchored
            and match_end(PatternSet(None, (), patterns.anchored), components, tolerant=False)
            is not None
        ):
            return Classification("regular", category=category)
        if patterns.single is not None and patterns.single.fullmatch(components[last]):
            return Classification("regular", category=category)
    return Classification("regular")

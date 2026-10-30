# DevOps Tooling Agent

An extensible DevOps engineering environment powered by [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

**DevOps Tooling Agent** aims to provide a structured, extensible environment for automating DevOps engineering workflows through reusable skills and deterministic integrations.

Rather than implementing an independent AI agent runtime, the project uses Claude Code as its primary execution engine and conversational interface, while Python provides the integration and automation layer.

> **Project status:** Early development (pre-alpha). The project foundation is being established. Features and interfaces described as planned are not necessarily implemented yet.

## Goals

- **AI-assisted DevOps workflows:** Use Claude Code to reason about engineering tasks and coordinate workflows.
- **Structured operations:** Provide reusable skills for common DevOps activities.
- **Deterministic integrations:** Implement external service interactions and automation utilities in Python.
- **Extensibility:** Support additional tools, platforms, and workflows without coupling the project to a specific infrastructure provider.
- **Human oversight:** Keep sensitive operations and consequential decisions under explicit human control.

## Architecture

DevOps Tooling Agent is a centralized Claude Code environment. Claude Code is started from this repository and operates on external target repositories, which do not need to contain or install anything from this project.

| Component | Responsibility |
|---|---|
| Claude Code | Reasoning, conversational interface, and workflow orchestration |
| Project instructions | Global agent conventions and engineering standards |
| Skills | Reusable capabilities, domain knowledge, and procedures, invoked as slash commands |
| Subagents | Optional specialist roles, introduced only when justified |
| Python package | Deterministic execution, integrations, validation, and automation |
| Target repositories | External repositories the agent works on, treated as untrusted input |

Claude Code remains the orchestration engine. Python does not implement a separate agent runtime, use LLM SDKs, or launch Claude Code as a subprocess.

See [`docs/architecture.md`](docs/architecture.md) for the authoritative architecture description and [`docs/adr/`](docs/adr/README.md) for architecture decision records.

## Requirements

The development environment requires:

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code)
- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)
- Git

The repository currently pins Python 3.13.2 for local development. CI is intended to validate compatibility with Python 3.12, 3.13, and 3.14.

## Getting started

Clone the repository:

```bash
git clone https://github.com/sierrapablo-dev/devops-tooling-agent.git
cd devops-tooling-agent
```

Install the project and development dependencies:

```bash
uv sync --locked --all-groups
```

Start Claude Code from the repository root:

```bash
claude
```

> The project is under active development. Skills will become available as their respective components are implemented.

## Development

Install dependencies:

```bash
uv sync --locked --all-groups
```

Run the quality checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run bandit -r src
uv run pip-audit --skip-editable
```

Run tests:

```bash
uv run pytest --strict-markers
```

Build the package:

```bash
uv build
```

## Project structure

```text
devops-tooling-agent/
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/
│   ├── adr/
│   │   ├── README.md
│   │   ├── 0001-claude-code-as-orchestration-engine.md
│   │   └── 0002-python-as-deterministic-integration-layer.md
│   └── architecture.md
├── src/
│   └── devops_tooling_agent/
│       ├── __init__.py
│       └── py.typed
├── tests/
│   └── test_package.py
├── .gitattributes
├── .gitignore
├── .python-version
├── CLAUDE.md
├── pyproject.toml
├── uv.lock
└── README.md
```

The structure will evolve as skills and Python integrations are introduced.

## Planned capabilities

The initial direction includes:

- GitHub repository and pull request workflows.
- Issue tracking integration.
- Structured implementation and code review workflows.
- Workspace and project management utilities.
- Reusable DevOps engineering skills.
- Extensible integrations with infrastructure and development platforms.

These capabilities represent the project's intended direction, not a guarantee of current availability.

## Development principles

- **Claude Code first:** Use its native capabilities instead of recreating agent orchestration.
- **Python for deterministic operations:** Keep integrations explicit, testable, and maintainable.
- **Least privilege:** Apply minimum permissions to external integrations and automation.
- **Human approval:** Require authorization for sensitive or irreversible operations.
- **Quality by default:** Maintain automated testing, linting, type checking, security analysis, and dependency auditing.
- **Incremental extensibility:** Introduce abstractions only when justified by concrete requirements.

## Contributing

The project is currently in its early development stage. Contributions, suggestions, and discussions are welcome, although interfaces and architecture may still change.

For substantial changes, open an issue first to discuss the proposed approach.

## License

This project is licensed under the [MIT License](LICENSE).
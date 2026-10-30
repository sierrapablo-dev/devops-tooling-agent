# DevOps Tooling Agent

An extensible DevOps engineering environment powered by [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

**DevOps Tooling Agent** aims to provide a structured, extensible environment for automating DevOps engineering workflows through specialized commands, reusable skills, and deterministic integrations.

Rather than implementing an independent AI agent runtime, the project uses Claude Code as its primary execution engine and conversational interface, while Python provides the integration and automation layer.

> **Project status:** Early development (pre-alpha). The project foundation is being established. Features and interfaces described as planned are not necessarily implemented yet.

## Goals

- **AI-assisted DevOps workflows:** Use Claude Code to reason about engineering tasks and coordinate workflows.
- **Structured operations:** Provide reusable commands and skills for common DevOps activities.
- **Deterministic integrations:** Implement external service interactions and automation utilities in Python.
- **Extensibility:** Support additional tools, platforms, and workflows without coupling the project to a specific infrastructure provider.
- **Human oversight:** Keep sensitive operations and consequential decisions under explicit human control.

## Architecture

The project follows a separation of responsibilities:

| Component | Responsibility |
|---|---|
| Claude Code | Conversational interface, reasoning, workflow orchestration, and code execution |
| Commands | User-facing entry points for specialized workflows |
| Skills | Reusable domain knowledge, instructions, and procedures |
| Python package | Deterministic integrations, utilities, and automation |
| External services | Infrastructure platforms, source control, issue trackers, and other DevOps tools |

The intended interaction model is:

```text
User
 │
 ▼
Claude Code
 │
 ├── Commands
 │    └── Skills
 │
 └── Python integrations
      │
      ├── GitHub
      ├── Issue trackers
      ├── Infrastructure platforms
      └── Other DevOps tools
```

Claude Code remains the execution engine. Python does not implement a separate agent runtime or launch Claude Code as a subprocess.

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

> The project is under active development. The command and skill interfaces will become available as their respective components are implemented.

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
├── src/
│   └── devops_tooling_agent/
│       ├── __init__.py
│       └── py.typed
├── tests/
│   └── test_package.py
├── .gitattributes
├── .gitignore
├── .python-version
├── pyproject.toml
├── uv.lock
└── README.md
```

The structure will evolve as commands, skills, and Python integrations are introduced.

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
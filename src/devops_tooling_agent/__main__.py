"""Module entry point: ``python -I -m devops_tooling_agent <group> <command> ...``."""

import sys

from devops_tooling_agent.cli import main

raise SystemExit(main(sys.argv[1:]))

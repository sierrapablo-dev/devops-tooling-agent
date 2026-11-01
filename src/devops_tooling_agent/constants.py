"""Operating values of the package.

Every value here is documented in docs/python-package.md, Section 4, and a test
keeps both equal. Change a value only through a reviewed pull request that
states which invariant of ADR-0006 and ADR-0007 it still satisfies.
"""

from typing import Final

# 4.1 Time
GH_CALL_TIMEOUT_SECONDS: Final = 30
RESOLVE_DEADLINE_SECONDS: Final = 60
COLLECT_TOOL_TIMEOUT_MS: Final = 300000
COLLECT_DEADLINE_SECONDS: Final = 240
CLEANUP_RESERVE_SECONDS: Final = 30
REMOVAL_ATTEMPTS: Final = 20
REMOVAL_RETRY_DELAY_SECONDS: Final = 0.5
TERMINATION_WAIT_SECONDS: Final = 10
GIT_CALL_TIMEOUT_SECONDS: Final = 30
SWEEP_MIN_AGE_SECONDS: Final = 3600

# 4.2 Acquisition
MAX_REPO_DISK_USAGE_KIB: Final = 1048576
MAX_WORKSPACE_BYTES: Final = 536870912
WORKSPACE_POLL_SECONDS: Final = 1
FETCH_DEPTH: Final = 20

# 4.4 Output
MAX_LINE_CHARS: Final = 512
MAX_ENVELOPE_CHARS: Final = 200000

# 4.5 Workspaces
WORKSPACE_PREFIX: Final = "devops-tooling-agent-"
WORKSPACE_SUFFIX_CHARS: Final = 32
MARKER_FILE_NAME: Final = ".devops-tooling-agent-workspace.json"
MARKER_ID: Final = "devops-tooling-agent-workspace"
MARKER_VERSION: Final = 1
MARKER_MAX_BYTES: Final = 1024
GLOBAL_CONFIG_FILE_NAME: Final = "gitconfig"
REPOSITORY_DIR_NAME: Final = "repository.git"
MAX_SWEEP_ENTRIES: Final = 200

# 4.6 Tool versions
GIT_MIN_VERSION: Final = "2.32.0"
GH_MIN_VERSION: Final = "2.40.0"

# 4.7 Validation
MAX_IDENTIFIER_CHARS: Final = 256
MAX_OWNER_CHARS: Final = 39
MAX_REPO_NAME_CHARS: Final = 100
MAX_BRANCH_NAME_CHARS: Final = 255

# 4.8 Subprocesses
GH_MAX_OUTPUT_BYTES: Final = 65536
GIT_MAX_OUTPUT_BYTES: Final = 65536

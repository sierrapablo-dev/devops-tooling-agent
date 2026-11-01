"""Operating values of the package.

Every value here is documented in docs/python-package.md, Section 4, and a test
keeps both equal. Change a value only through a reviewed pull request that
states which invariant of ADR-0006 and ADR-0007 it still satisfies.
"""

from typing import Final

# 4.1 Time
GH_CALL_TIMEOUT_SECONDS: Final = 30
RESOLVE_DEADLINE_SECONDS: Final = 60

# 4.4 Output
MAX_LINE_CHARS: Final = 512
MAX_ENVELOPE_CHARS: Final = 200000

# 4.6 Tool versions
GH_MIN_VERSION: Final = "2.40.0"

# 4.7 Validation
MAX_IDENTIFIER_CHARS: Final = 256
MAX_OWNER_CHARS: Final = 39
MAX_REPO_NAME_CHARS: Final = 100
MAX_BRANCH_NAME_CHARS: Final = 255

# 4.8 Subprocesses
GH_MAX_OUTPUT_BYTES: Final = 65536

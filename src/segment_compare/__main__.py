"""CLI entry point.

Phase 0 placeholder. The full CLI lands in Phase 1 (see
``docs/phase-1.md``).
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Entry point shim.

    Returns a non-zero exit code until Phase 1 implementation lands.
    """
    del argv
    sys.stderr.write(
        "segment-compare CLI is not implemented yet. See docs/phase-1.md.\n"
    )
    return 30


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

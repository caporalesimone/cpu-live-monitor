"""Runtime Python version guard.

This module must be importable and runnable before any other project import so
that an unsupported interpreter fails fast with a clear message.
"""

from __future__ import annotations

import sys

_MIN_VERSION: tuple[int, int] = (3, 12)
_MAX_VERSION_EXCLUSIVE: tuple[int, int] = (3, 13)
_REQUIRED = ">=3.12,<3.13"


def ensure_supported_python() -> None:
    """Exit with an error if the running interpreter is out of range."""
    current = sys.version_info[:2]
    if not (_MIN_VERSION <= current < _MAX_VERSION_EXCLUSIVE):
        found = ".".join(str(part) for part in sys.version_info[:3])
        sys.stderr.write(f"cpumon requires Python {_REQUIRED}; found {found}\n")
        raise SystemExit(1)

"""Process exit codes for the cpumon CLI."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable exit codes returned by the CLI."""

    SUCCESS = 0
    ERROR = 1
    MISSING_INPUT = 2

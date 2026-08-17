"""Tunable runtime limits shared by the collectors, the UI and the CLI.

Kept dependency-free so any layer may import it without creating a cycle.
"""

from __future__ import annotations

from typing import Final

# Sampling interval: a linear 0.1 s step between two hard bounds. Values are
# rounded to one decimal at every change so repeated F2/F3 presses cannot
# accumulate binary float drift (0.7000000000000001 and friends).
INTERVAL_MIN: Final[float] = 0.5
INTERVAL_MAX: Final[float] = 10.0
INTERVAL_STEP: Final[float] = 0.1

# Samples retained per series. It matches MAX_HISTORY, the widest trend the
# layout will ever draw, so a full-width window is never short of data.
HISTORY_CAPACITY: Final[int] = 400


def clamp_interval(value: float) -> float:
    """Round *value* to one decimal and hold it inside the allowed range."""
    return round(min(INTERVAL_MAX, max(INTERVAL_MIN, value)), 1)

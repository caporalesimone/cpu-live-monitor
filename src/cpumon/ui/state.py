"""Mutable UI state: what the user has asked the display to do.

Owned by the runtime layer, read by the view builder. Deliberately free of
metric data — samples live in the history store, memory in its collector.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class UiState:
    """Everything a key press can change."""

    interval: float = 1.0
    uptime: float = 0.0
    help_visible: bool = False
    help_scroll: int = 0
    running: bool = True

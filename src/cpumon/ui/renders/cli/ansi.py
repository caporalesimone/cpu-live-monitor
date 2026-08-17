"""The handful of ANSI control sequences the app emits."""

from __future__ import annotations

from typing import Final

CSI: Final = "\x1b["

CLEAR_SCREEN: Final = f"{CSI}2J"
CLEAR_LINE: Final = f"{CSI}2K"
HOME: Final = f"{CSI}H"
HIDE_CURSOR: Final = f"{CSI}?25l"
SHOW_CURSOR: Final = f"{CSI}?25h"


def at(row: int) -> str:
    """Move to the start of *row* (0-based) and clear it."""
    return f"{CSI}{row + 1};1H{CLEAR_LINE}"


def move(row: int, column: int) -> str:
    """Move to *row* (0-based) and *column* (1-based, as the terminal counts)."""
    return f"{CSI}{row + 1};{column}H"

"""Unicode building blocks used by the renderer."""

from __future__ import annotations

from typing import ClassVar


class Glyph:
    """Every non-ASCII character the app draws."""

    # Horizontal bar: full cell plus the seven left-anchored partial cells.
    # U+2588 is the full block; U+2590 - n gives n/8 of a cell, n = 1..7.
    FULL = "█"
    PARTIAL: ClassVar[tuple[str, ...]] = tuple(chr(0x2590 - n) for n in range(1, 8))

    # Vertical bar: eight levels, U+2581 (1/8) .. U+2588 (8/8).
    SPARK: ClassVar[tuple[str, ...]] = tuple(chr(0x2580 + n) for n in range(1, 9))

    H = "─"  # light horizontal
    V = "│"  # light vertical
    CROSS = "┼"  # light cross, rule meeting a column separator
    TEE_DOWN = "┬"  # light down-and-horizontal, top edge of the table
    TEE_UP = "┴"  # light up-and-horizontal, bottom edge of the table
    ARROW = "►"  # black right-pointing pointer
    ARROW_LEFT = "◄"  # black left-pointing pointer
    SEAM = "┊"  # light quadruple dash vertical, marks a time-base break
    DOT = "·"  # middle dot
    COPY = "©"  # copyright sign

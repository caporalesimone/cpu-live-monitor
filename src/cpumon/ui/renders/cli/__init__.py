"""Character-cell terminal renderer.

Everything ANSI, every glyph and every column width lives in this subpackage and
nowhere else in the program:

    ansi.py        the control sequences that get emitted
    glyphs.py      the unicode building blocks
    theme.py       every colour, in one place
    palette.py     load value -> colour, precomputed per percent
    formatting.py  numbers and durations as text of a bounded width
    layout.py      the responsive geometry solver
    planner.py     which rows fit, and which series feed them
    trend.py       sample-and-marker layout of one trend row
    widgets.py     gauge and sparkline lookup tables
    clock.py       the wall clock, as wide as it can be
    helpstyle.py   help content -> styled lines
    renderer.py    frame composition
"""

from __future__ import annotations

from cpumon.ui.renders.cli.renderer import CliPlan, CliRenderer

__all__ = ["CliPlan", "CliRenderer"]

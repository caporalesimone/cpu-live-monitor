"""Layout of one trend row: which cells hold samples and which hold markers.

The plan is a rendering concern, so it lives here rather than in the history
store: the store knows *what* was sampled and when the cadence changed, this
module decides how that maps onto screen cells.
"""

from __future__ import annotations

from typing import NamedTuple

from cpumon.core.history import MarkerState
from cpumon.ui.renders.cli.glyphs import Glyph

# (label character, seam character) for a cell that carries a marker instead
# of a sample. Both variants are precomputed because the label is spelled out
# on one row only and every other row draws the bare seam.
TrendCell = tuple[str, str]


class TrendPlan(NamedTuple):
    """How one row of trend is laid out.

    cells   one entry per column: None for a sample, otherwise the
            (label_char, seam_char) of a marker or of left padding
    samples how many sample values the plan consumes
    pad     leading columns that hold nothing, because the history is still
            shorter than the window
    """

    cells: tuple[TrendCell | None, ...]
    samples: int
    pad: int

    @classmethod
    def empty(cls) -> TrendPlan:
        return cls((), 0, 0)


def build_trend_plan(width: int, state: MarkerState) -> TrendPlan:
    """Lay out *width* trend cells, oldest first.

    A marker is not painted over the data: it takes cells of its own and
    pushes everything older one block to the left. No sample is ever hidden —
    the oldest ones simply fall off the left edge, exactly as they would with
    the passage of time.
    """
    if width <= 0:
        return TrendPlan.empty()

    marks = dict(state.markers)
    cells: list[TrendCell | None] = []

    def push_marker(label: str) -> None:
        seam = (" " * (len(label) // 2) + Glyph.SEAM).ljust(len(label))
        for i in range(len(label) - 1, -1, -1):
            if len(cells) >= width:
                return
            cells.append((label[i], seam[i]))

    # A marker pinned to the not-yet-taken sample shows immediately, so
    # pressing a key gives feedback without waiting a whole period.
    if state.sequence in marks:
        push_marker(marks[state.sequence])

    oldest = state.sequence - state.count
    seq = state.sequence - 1
    while len(cells) < width and seq >= oldest:
        cells.append(None)
        if seq in marks:
            push_marker(marks[seq])
        seq -= 1

    cells.reverse()
    pad = max(0, width - len(cells))
    if pad:
        padding: list[TrendCell | None] = [(" ", " ")] * pad
        cells = padding + cells
    return TrendPlan(tuple(cells), sum(1 for cell in cells if cell is None), pad)

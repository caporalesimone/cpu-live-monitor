"""Gauge and sparkline renderers, driven by lookup tables.

Both tables are built once at import time. At render time there is no
arithmetic per cell, only array indexing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from cpumon.ui.renders.cli.formatting import clamp_percent
from cpumon.ui.renders.cli.glyphs import Glyph
from cpumon.ui.renders.cli.layout import W_GAUGE
from cpumon.ui.renders.cli.palette import LoadPalette
from cpumon.ui.renders.cli.theme import Theme
from cpumon.ui.renders.cli.trend import TrendCell

_EIGHTHS = 8


class GaugeLut:
    """Horizontal bar, `cells` wide, with 1/8-cell resolution.

    The empty part is padded with spaces rather than a shading glyph: a
    partial block covers only a fraction of its cell and leaves the terminal
    background showing, so an adjacent shaded cell produces a visible seam.
    """

    def __init__(self, cells: int) -> None:
        self.cells = cells
        self.fill: list[str] = []
        self.pad: list[str] = []
        for percent in range(101):
            eighths = round(percent / 100.0 * cells * _EIGHTHS)
            full, rem = divmod(eighths, _EIGHTHS)
            if full >= cells:
                full, rem = cells, 0
            text = Glyph.FULL * full
            if rem:
                text += Glyph.PARTIAL[rem - 1]
            self.fill.append(text)
            self.pad.append(" " * (cells - len(text)))

    def render(self, value: float, palette: LoadPalette) -> str:
        idx = clamp_percent(value)
        return palette.colour[idx] + self.fill[idx] + Theme.RESET + self.pad[idx]


class SparkLut:
    """Vertical eight-level sparkline with a colour per sample."""

    def __init__(self) -> None:
        # Glyph height is metric-independent; only the colour differs.
        self.glyph: list[str] = [
            Glyph.SPARK[min(7, max(0, -(-p * _EIGHTHS // 100) - 1))] for p in range(101)
        ]

    def render(
        self,
        cells: Sequence[TrendCell | None],
        samples: Sequence[float],
        palette: LoadPalette,
        marker_colour: str = "",
        *,
        with_label: bool = False,
    ) -> str:
        """Draw the cells described by a :class:`~cpumon.ui.renders.cli.trend.TrendPlan`.

        Sample slots consume `samples` in order; marker slots draw their own
        character. `with_label` picks the spelled-out variant (one row) over
        the bare seam (every other row).
        """
        if not cells:
            return ""
        colours = palette.colour
        glyphs = self.glyph
        parts: list[str] = []
        current = ""
        index = 0

        for entry in cells:
            if entry is None:
                value = samples[index] if index < len(samples) else 0.0
                index += 1
                idx = clamp_percent(value)
                colour, char = colours[idx], glyphs[idx]
            else:
                char = entry[0] if with_label else entry[1]
                colour = marker_colour if char != " " else ""
            if colour != current:
                parts.append(colour if colour else Theme.RESET)
                current = colour
            parts.append(char)

        parts.append(Theme.RESET)
        return "".join(parts)


def gauge_scale_label(cells: int) -> str:
    """Tick row above the gauge: ends labelled, midpoint marked with a dot.

    Built rather than hard-coded so a change to W_GAUGE cannot silently
    desynchronise it from the bar below.
    """
    label = [" "] * cells
    left, right = "0%", "100%"
    if len(left) + len(right) + 1 > cells:
        return "".join(label)
    for i, ch in enumerate(left):
        label[i] = ch
    start = cells - len(right)
    for i, ch in enumerate(right):
        label[start + i] = ch
    centre = (cells - 1) // 2
    if label[centre] == " ":
        label[centre] = Glyph.DOT
    return "".join(label)


GAUGE: Final = GaugeLut(W_GAUGE)
SPARK: Final = SparkLut()
GAUGE_SCALE_LABEL: Final = gauge_scale_label(W_GAUGE)

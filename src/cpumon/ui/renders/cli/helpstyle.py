"""Styling of the help document for a terminal.

Each :class:`~cpumon.ui.help.HelpLine` becomes exactly one output line, so the
document's scroll arithmetic holds without the content knowing anything about
colours. Both variants are produced: the renderer needs the plain one to measure
a line, because styled text can never be sliced — a cut inside an escape
sequence leaks raw bytes onto the screen.
"""

from __future__ import annotations

from typing import Final

from cpumon.ui.help import HelpLine, HelpLineKind
from cpumon.ui.model import MetricKind
from cpumon.ui.renders.cli.formatting import clamp_percent
from cpumon.ui.renders.cli.glyphs import Glyph
from cpumon.ui.renders.cli.palette import LoadPalette, palette_for
from cpumon.ui.renders.cli.theme import Theme
from cpumon.ui.renders.cli.trend import TrendCell
from cpumon.ui.renders.cli.widgets import GAUGE, SPARK

StyledLine = tuple[str, str]  # (plain, styled)

# Hardcoded sample values for the gauge legend. 45 is deliberate: it is the one
# point where the two scales disagree (amber for CPU, still green for RAM), so
# the example rows visibly differ instead of looking identical.
_GAUGE_SAMPLES: Final[tuple[int, ...]] = (20, 45, 85)
_LABEL_COLUMN: Final = 6
_SWATCH: Final = 4
_BAND_COLUMN: Final = 10
_RAMP_STEPS: Final = 16


def style_help(line: HelpLine) -> StyledLine:
    """One help line as (plain, styled)."""
    if line.kind is HelpLineKind.BLANK:
        return "", ""
    if line.kind is HelpLineKind.TITLE:
        return _flat(line.text, Theme.HELP_TITLE)
    if line.kind is HelpLineKind.SUBTLE:
        return _flat(line.text, Theme.SUBTITLE)
    if line.kind is HelpLineKind.CREDIT:
        text = f"{line.label} {Glyph.DOT} {Glyph.COPY} {line.text}"
        return _flat(text, Theme.SUBTITLE)
    if line.kind is HelpLineKind.CORE_CLASS:
        return _core_class(line)
    if line.kind is HelpLineKind.COLOUR_BANDS:
        return _colour_bands(line)
    if line.kind is HelpLineKind.GAUGE_SCALE:
        return _gauge_scale(line)
    if line.kind is HelpLineKind.TREND_RAMP:
        return _trend_ramp(line)
    return _flat(line.text, Theme.HELP_BODY)


def _flat(text: str, colour: str) -> StyledLine:
    return text, colour + text + Theme.RESET


def _core_class(line: HelpLine) -> StyledLine:
    padded = line.tag.ljust(_LABEL_COLUMN)
    styled = (
        Theme.class_colour(line.tag)
        + padded
        + Theme.RESET
        + Theme.HELP_BODY
        + line.text
        + Theme.RESET
    )
    return f"{padded}{line.text}", styled


def _palette(metric: MetricKind | None) -> LoadPalette:
    return palette_for(metric if metric is not None else MetricKind.CPU)


def _colour_bands(line: HelpLine) -> StyledLine:
    """Every colour band of one metric, with its range."""
    plain = line.label.ljust(_LABEL_COLUMN)
    styled = Theme.HELP_BODY + plain + Theme.RESET
    for low, high, colour in _palette(line.metric).bands():
        swatch = Glyph.FULL * _SWATCH
        text = f" {low}-{high}%".ljust(_BAND_COLUMN)
        plain += swatch + text
        styled += colour + swatch + Theme.RESET + Theme.HELP_BODY + text
    return plain, styled + Theme.RESET


def _gauge_scale(line: HelpLine) -> StyledLine:
    """The gauge of one metric at one value per band."""
    palette = _palette(line.metric)
    plain = line.label.ljust(_LABEL_COLUMN)
    styled = Theme.HELP_BODY + plain + Theme.RESET
    for value in _GAUGE_SAMPLES:
        tail = f" {value:>3}%   "
        plain += GAUGE.fill[value] + GAUGE.pad[value] + tail
        styled += GAUGE.render(value, palette) + Theme.HELP_BODY + tail
    return plain, styled + Theme.RESET


def _trend_ramp(line: HelpLine) -> StyledLine:
    ramp = [i * 100 / (_RAMP_STEPS - 1) for i in range(_RAMP_STEPS)]
    label = f"{line.label} "
    plain = label + "".join(SPARK.glyph[clamp_percent(v)] for v in ramp) + line.text
    # A plan of plain sample slots: the legend has no markers in it.
    cells: tuple[TrendCell | None, ...] = (None,) * len(ramp)
    styled = (
        Theme.HELP_BODY
        + label
        + Theme.RESET
        + SPARK.render(cells, ramp, palette_for(MetricKind.CPU))
        + Theme.HELP_BODY
        + line.text
        + Theme.RESET
    )
    return plain, styled

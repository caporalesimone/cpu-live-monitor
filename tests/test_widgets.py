"""Widgets are lookup-table driven: the tables are what needs guarding."""

from __future__ import annotations

from itertools import pairwise

from cpumon.ui.renders.cli.glyphs import Glyph
from cpumon.ui.renders.cli.layout import W_GAUGE
from cpumon.ui.renders.cli.palette import PALETTE_CPU, PALETTE_MEM
from cpumon.ui.renders.cli.theme import Theme
from cpumon.ui.renders.cli.trend import TrendCell
from cpumon.ui.renders.cli.widgets import GAUGE, GAUGE_SCALE_LABEL, SPARK, gauge_scale_label
from tests.conftest import plain


def test_gauge_cells_always_add_up_to_the_column_width() -> None:
    for percent in range(101):
        assert len(GAUGE.fill[percent]) + len(GAUGE.pad[percent]) == W_GAUGE


def test_gauge_is_monotonic() -> None:
    widths = [len(GAUGE.fill[p]) for p in range(101)]
    assert widths == sorted(widths)
    assert GAUGE.fill[0] == ""
    assert GAUGE.fill[100] == Glyph.FULL * W_GAUGE


def test_gauge_pads_with_spaces_not_shading() -> None:
    """A partial block leaves the background showing; shading would seam."""
    assert set(GAUGE.pad[50]) <= {" "}


def test_gauge_render_is_one_visible_column_per_cell() -> None:
    for percent in (0, 1, 45, 99, 100):
        assert len(plain(GAUGE.render(percent, PALETTE_CPU))) == W_GAUGE


def test_spark_glyphs_span_the_eight_levels() -> None:
    assert SPARK.glyph[0] == Glyph.SPARK[0]
    assert SPARK.glyph[100] == Glyph.SPARK[7]
    heights = [Glyph.SPARK.index(SPARK.glyph[p]) for p in range(101)]
    assert heights == sorted(heights)


def test_spark_renders_one_cell_per_plan_entry() -> None:
    cells: tuple[TrendCell | None, ...] = (None, None, ("x", Glyph.SEAM), None)
    out = SPARK.render(cells, [10.0, 20.0, 30.0], PALETTE_CPU, Theme.MARKER)
    assert len(plain(out)) == len(cells)


def test_spark_draws_the_label_only_when_asked() -> None:
    cells: tuple[TrendCell | None, ...] = (("2", Glyph.SEAM),)
    bare = plain(SPARK.render(cells, [], PALETTE_CPU, Theme.MARKER))
    labelled = plain(SPARK.render(cells, [], PALETTE_CPU, Theme.MARKER, with_label=True))
    assert bare == Glyph.SEAM
    assert labelled == "2"


def test_spark_tolerates_fewer_samples_than_cells() -> None:
    cells: tuple[TrendCell | None, ...] = (None,) * 5
    assert len(plain(SPARK.render(cells, [50.0], PALETTE_CPU))) == 5


def test_spark_of_an_empty_plan_is_empty() -> None:
    assert SPARK.render((), [], PALETTE_CPU) == ""


def test_gauge_scale_label_matches_the_gauge_width() -> None:
    assert len(GAUGE_SCALE_LABEL) == W_GAUGE
    assert GAUGE_SCALE_LABEL.startswith("0%")
    assert GAUGE_SCALE_LABEL.endswith("100%")
    assert Glyph.DOT in GAUGE_SCALE_LABEL


def test_gauge_scale_label_degrades_when_there_is_no_room() -> None:
    assert gauge_scale_label(6) == " " * 6  # "0%" + "100%" + a gap does not fit
    assert len(gauge_scale_label(7)) == 7


def test_palettes_cover_every_percent_and_differ_where_intended() -> None:
    for palette in (PALETTE_CPU, PALETTE_MEM):
        assert len(palette.colour) == 101
        assert all(palette.colour)
    # 45% is amber for the CPU but still green for memory.
    assert PALETTE_CPU.colour[45] == Theme.WARN_
    assert PALETTE_MEM.colour[45] == Theme.OK


def test_palette_bands_are_contiguous() -> None:
    for palette in (PALETTE_CPU, PALETTE_MEM):
        bands = palette.bands()
        assert bands[0][0] == 0
        assert bands[-1][1] == 100
        for (_low, high, _c), (next_low, _h, _c2) in pairwise(bands):
            assert next_low == high + 1

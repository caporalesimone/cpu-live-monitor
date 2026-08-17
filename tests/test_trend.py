"""Trend layout: markers take cells of their own, they never hide a sample."""

from __future__ import annotations

from cpumon.core.history import MarkerState
from cpumon.ui.renders.cli.glyphs import Glyph
from cpumon.ui.renders.cli.trend import build_trend_plan


def test_zero_width_is_empty() -> None:
    plan = build_trend_plan(0, MarkerState(sequence=10, count=10))
    assert plan == ((), 0, 0)


def test_full_window_is_all_samples() -> None:
    plan = build_trend_plan(10, MarkerState(sequence=50, count=50))
    assert len(plan.cells) == 10
    assert plan.samples == 10
    assert plan.pad == 0
    assert all(cell is None for cell in plan.cells)


def test_short_history_pads_on_the_left() -> None:
    plan = build_trend_plan(10, MarkerState(sequence=3, count=3))
    assert plan.samples == 3
    assert plan.pad == 7
    assert plan.cells[:7] == ((" ", " "),) * 7
    assert plan.cells[7:] == (None, None, None)


def test_marker_consumes_cells_and_pushes_history_left() -> None:
    label = " 2.0 "
    plan = build_trend_plan(20, MarkerState(50, 50, ((45, label),)))
    assert len(plan.cells) == 20
    # The marker takes as many cells as its label is long.
    assert plan.samples == 20 - len(label)
    labels = "".join(cell[0] for cell in plan.cells if cell is not None)
    assert labels == label


def test_marker_draws_a_seam_on_rows_without_the_label() -> None:
    plan = build_trend_plan(20, MarkerState(50, 50, ((45, " 2.0 "),)))
    seams = "".join(cell[1] for cell in plan.cells if cell is not None)
    assert seams.count(Glyph.SEAM) == 1
    assert seams.strip() == Glyph.SEAM


def test_marker_on_the_next_sample_shows_at_once() -> None:
    """Pressing a key must give feedback without waiting a whole period."""
    plan = build_trend_plan(20, MarkerState(50, 50, ((50, " 0.5 "),)))
    assert plan.cells[-1] is not None  # the newest cells carry the label


def test_marker_is_clipped_at_the_left_edge() -> None:
    plan = build_trend_plan(3, MarkerState(50, 50, ((50, " 10.0 "),)))
    assert len(plan.cells) == 3
    assert plan.samples == 0


def test_every_cell_is_either_a_sample_or_a_marker() -> None:
    plan = build_trend_plan(40, MarkerState(60, 60, ((30, " 1.0 "), (50, " 2.0 "))))
    assert len(plan.cells) == 40
    assert plan.samples == sum(1 for cell in plan.cells if cell is None)
    assert plan.samples + sum(1 for cell in plan.cells if cell is not None) == 40


def test_empty_history_yields_only_padding() -> None:
    plan = build_trend_plan(5, MarkerState(sequence=0, count=0))
    assert plan.samples == 0
    assert plan.pad == 5

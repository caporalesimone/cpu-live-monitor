"""The layout solver is pure, so every mode transition is checked directly."""

from __future__ import annotations

import pytest

from cpumon.core.model import Topology
from cpumon.ui.renders.cli.layout import (
    MAX_HISTORY,
    MIN_HISTORY,
    W_CPU_USAGE,
    W_WITH_GAUGE,
    W_WITH_HISTORY,
    W_WITH_TYPE,
    ColMode,
    LayoutSolver,
    RowMode,
)

TALL = 200


@pytest.mark.parametrize(
    ("cols", "expected"),
    [
        (W_WITH_HISTORY + MIN_HISTORY, ColMode.FULL),
        (W_WITH_HISTORY + MIN_HISTORY - 1, ColMode.NO_HISTORY),
        (W_WITH_GAUGE, ColMode.NO_HISTORY),
        (W_WITH_GAUGE - 1, ColMode.NO_GAUGE),
        (W_WITH_TYPE, ColMode.NO_GAUGE),
        (W_WITH_TYPE - 1, ColMode.NO_TYPE),
        (W_CPU_USAGE, ColMode.NO_TYPE),
        (W_CPU_USAGE - 1, ColMode.TOO_NARROW),
        (1, ColMode.TOO_NARROW),
    ],
)
def test_column_mode_thresholds(hybrid_topology: Topology, cols: int, expected: ColMode) -> None:
    geom = LayoutSolver(hybrid_topology).solve(cols, TALL)
    assert geom.col_mode is expected


def test_history_width_absorbs_the_extra_columns(hybrid_topology: Topology) -> None:
    solver = LayoutSolver(hybrid_topology)
    for cols in range(W_WITH_HISTORY + MIN_HISTORY, 300):
        geom = solver.solve(cols, TALL)
        assert geom.history_width == min(MAX_HISTORY, cols - W_WITH_HISTORY)
        assert geom.line_width == W_WITH_HISTORY + geom.history_width
        assert geom.line_width <= cols


def test_history_width_is_capped(hybrid_topology: Topology) -> None:
    geom = LayoutSolver(hybrid_topology).solve(2000, TALL)
    assert geom.history_width == MAX_HISTORY


def test_line_width_never_exceeds_the_window(hybrid_topology: Topology) -> None:
    solver = LayoutSolver(hybrid_topology)
    for cols in range(W_CPU_USAGE, 200):
        assert solver.solve(cols, TALL).line_width <= cols


def test_tall_window_shows_every_thread(hybrid_topology: Topology) -> None:
    geom = LayoutSolver(hybrid_topology).solve(200, TALL)
    assert geom.row_mode is RowMode.PER_CPU
    assert geom.body_rows == hybrid_topology.n_cpus
    assert geom.summary_rows == 1 + len(hybrid_topology.classes)


def test_rows_degrade_monotonically(hybrid_topology: Topology) -> None:
    """Shrinking the window may only ever show less, never more."""
    solver = LayoutSolver(hybrid_topology)
    order = {
        RowMode.PER_CPU: 4,
        RowMode.PER_GROUP: 3,
        RowMode.PER_CLASS: 2,
        RowMode.TOTAL_ONLY: 1,
        RowMode.TOO_SHORT: 0,
    }
    previous = order[RowMode.PER_CPU]
    for rows in range(TALL, 0, -1):
        rank = order[solver.solve(200, rows).row_mode]
        assert rank <= previous
        previous = rank


def test_every_mode_is_reachable(hybrid_topology: Topology) -> None:
    solver = LayoutSolver(hybrid_topology)
    seen = {solver.solve(200, rows).row_mode for rows in range(1, TALL)}
    assert seen == set(RowMode)


def test_folding_uses_progressively_larger_buckets(hybrid_topology: Topology) -> None:
    solver = LayoutSolver(hybrid_topology)
    sizes = [
        solver.solve(200, rows).group_size
        for rows in range(1, TALL)
        if solver.solve(200, rows).row_mode is RowMode.PER_GROUP
    ]
    assert sizes == sorted(sizes, reverse=True)
    assert max(sizes) > 1


def test_folded_rows_match_the_bucket_arithmetic(hybrid_topology: Topology) -> None:
    solver = LayoutSolver(hybrid_topology)
    for rows in range(1, TALL):
        geom = solver.solve(200, rows)
        if geom.row_mode is RowMode.PER_GROUP:
            assert geom.body_rows == solver.rows_for_group(geom.group_size)


def test_rows_fit_the_window(hybrid_topology: Topology) -> None:
    """The solver must never ask for more rows than the terminal has."""
    for backing in (False, True):
        solver = LayoutSolver(hybrid_topology, has_backing=backing)
        for rows in range(1, TALL):
            geom = solver.solve(200, rows)
            if not geom.usable:
                continue
            assert geom.summary_rows + geom.body_rows <= rows


def test_backing_row_costs_one_row(hybrid_topology: Topology) -> None:
    plain = LayoutSolver(hybrid_topology, has_backing=False)
    swap = LayoutSolver(hybrid_topology, has_backing=True)
    assert plain.solve(200, 30).show_backing is False
    assert swap.solve(200, 30).show_backing is True
    # The same window shows at most as much detail once a row is given up.
    for rows in range(1, TALL):
        assert swap.solve(200, rows).body_rows <= plain.solve(200, rows).body_rows


def test_uniform_machine_has_no_per_class_rows(uniform_topology: Topology) -> None:
    solver = LayoutSolver(uniform_topology)
    geom = solver.solve(200, TALL)
    assert geom.summary_rows == 1  # TOTAL only; classes would repeat it
    assert RowMode.PER_CLASS not in {solver.solve(200, rows).row_mode for rows in range(1, TALL)}

"""The frame builder: the only bridge between collected data and display.

What matters here is that it answers exactly what was asked for and nothing
more — no colours, no widths, no opinion about layout.
"""

from __future__ import annotations

import pytest

from cpumon.core.history import SeriesKey
from cpumon.core.model import Topology
from cpumon.ui.builder import FrameBuilder
from cpumon.ui.model import (
    FrameRequest,
    MetricKind,
    RowKind,
    RowSpec,
    ScreenKind,
)
from cpumon.ui.state import UiState
from tests.conftest import all_series_keys, filled_history

TOTAL_ROW = RowSpec(kind=RowKind.TOTAL, label="TOTAL", detail="20T", series_key=SeriesKey.TOTAL)
RAM_ROW = RowSpec(
    kind=RowKind.MEMORY,
    label="RAM",
    detail="32GB",
    series_key=SeriesKey.MEMORY,
    metric=MetricKind.MEMORY,
)


def builder(topo: Topology, samples: int = 60) -> FrameBuilder:
    return FrameBuilder(topo, filled_history(all_series_keys(topo), samples))


def test_machine_facts_come_from_the_topology(hybrid_topology: Topology) -> None:
    machine = builder(hybrid_topology).machine
    assert machine.name == hybrid_topology.model_name
    assert machine.cores == hybrid_topology.n_cores
    assert machine.threads == hybrid_topology.n_cpus
    assert machine.hybrid is True


def test_state_is_copied_into_the_model(hybrid_topology: Topology) -> None:
    state = UiState(interval=2.5, uptime=1234.0)
    model = builder(hybrid_topology).build(
        FrameRequest(ScreenKind.DASHBOARD, rows=(TOTAL_ROW,), sample_count=10), state
    )
    assert model.interval == 2.5
    assert model.uptime == 1234.0


def test_every_requested_row_comes_back_in_order(hybrid_topology: Topology) -> None:
    rows = (TOTAL_ROW, RAM_ROW)
    model = builder(hybrid_topology).build(
        FrameRequest(ScreenKind.DASHBOARD, rows=rows, sample_count=5), UiState()
    )
    assert tuple(row.spec for row in model.rows) == rows
    assert all(len(row.samples) == 5 for row in model.rows)


def test_values_and_samples_come_from_the_history(hybrid_topology: Topology) -> None:
    history = filled_history(all_series_keys(hybrid_topology), 30)
    model = FrameBuilder(hybrid_topology, history).build(
        FrameRequest(ScreenKind.DASHBOARD, rows=(TOTAL_ROW,), sample_count=8), UiState()
    )
    row = model.rows[0]
    assert row.value == history.latest(SeriesKey.TOTAL)
    assert list(row.samples) == history.tail(SeriesKey.TOTAL, 8)
    # The newest sample is the last cell, on every row without exception.
    assert row.samples[-1] == row.value


def test_a_request_without_samples_fetches_none(hybrid_topology: Topology) -> None:
    model = builder(hybrid_topology).build(
        FrameRequest(ScreenKind.DASHBOARD, rows=(TOTAL_ROW,), sample_count=0), UiState()
    )
    assert model.rows[0].samples == ()
    assert model.rows[0].value > 0  # the latest value is still cheap to report


def test_an_empty_request_costs_nothing(hybrid_topology: Topology) -> None:
    model = builder(hybrid_topology).build(FrameRequest(ScreenKind.HELP), UiState())
    assert model.rows == ()
    assert model.history_span == 0.0
    assert model.machine.threads == hybrid_topology.n_cpus


def test_the_span_is_measured_over_the_samples_drawn(hybrid_topology: Topology) -> None:
    # 20 samples 0.75 s apart cover 15 s.
    model = builder(hybrid_topology, 20).build(
        FrameRequest(
            ScreenKind.DASHBOARD,
            rows=(TOTAL_ROW,),
            sample_count=20,
            span_key=SeriesKey.TOTAL,
        ),
        UiState(),
    )
    assert model.history_span == pytest.approx(15.0)


def test_no_span_key_means_no_span(hybrid_topology: Topology) -> None:
    model = builder(hybrid_topology).build(
        FrameRequest(ScreenKind.DASHBOARD, rows=(TOTAL_ROW,), sample_count=10), UiState()
    )
    assert model.history_span == 0.0


def test_an_unknown_series_reads_as_empty(hybrid_topology: Topology) -> None:
    """A renderer asking for a series nobody collects must not explode."""
    ghost = RowSpec(RowKind.CLASS, "X", "0T", "cpu:class:X")
    model = builder(hybrid_topology).build(
        FrameRequest(ScreenKind.DASHBOARD, rows=(ghost,), sample_count=5), UiState()
    )
    assert model.rows[0].value == 0.0
    assert model.rows[0].samples == ()


def test_rows_can_be_selected_by_kind(hybrid_topology: Topology) -> None:
    model = builder(hybrid_topology).build(
        FrameRequest(ScreenKind.DASHBOARD, rows=(TOTAL_ROW, RAM_ROW), sample_count=1),
        UiState(),
    )
    assert model.rows_of_kind(RowKind.TOTAL) == (model.rows[0],)
    assert model.first_of_kind(RowKind.MEMORY) is model.rows[1]
    assert model.first_of_kind(RowKind.BACKING) is None

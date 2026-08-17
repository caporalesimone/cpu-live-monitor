"""The numbers on screen must add up.

An aggregate row claims to summarise the rows below it. These tests take the
whole path a frame really travels — sampler, collector, history, planner,
builder — and check that claim on the values the renderer would print, for every
layout the solver can choose.
"""

from __future__ import annotations

import pytest

from cpumon.core.collectors import CpuCollector
from cpumon.core.history import HistoryStore, SeriesKey
from cpumon.core.model import CoreClass, MemoryInfo, Topology
from cpumon.core.topology import core_buckets
from cpumon.settings import HISTORY_CAPACITY
from cpumon.ui.builder import FrameBuilder
from cpumon.ui.model import FrameModel, RowKind, Viewport
from cpumon.ui.renders.cli import CliRenderer
from cpumon.ui.renders.cli.formatting import fmt_percent
from cpumon.ui.state import UiState
from tests.conftest import FakeSampler

# Deliberately uneven, and deliberately not round: rounding must not be what
# makes the arithmetic work.
LOAD = [
    97.3, 3.1, 55.5, 12.8, 88.2, 41.9, 0.0, 100.0, 63.4, 7.7,
    29.6, 74.1, 18.2, 92.5, 50.0, 33.3, 66.7, 5.4, 81.9, 44.4,
]  # fmt: skip

MEMORY = MemoryInfo(total=32 * 2**30, available=12 * 2**30)


def build(topology: Topology, cols: int, rows: int) -> FrameModel:
    """One frame, through the same steps the application performs."""
    history = HistoryStore(HISTORY_CAPACITY)
    collector = CpuCollector(FakeSampler(LOAD[: topology.n_cpus]), topology)
    history.ensure_many(collector.series_keys())
    for i in range(3):
        history.push(collector.collect(), at=float(i))

    state = UiState()
    renderer = CliRenderer(topology)
    plan = renderer.plan(Viewport(cols, rows), state, MEMORY, history.marker_state())
    return FrameBuilder(topology, history).build(plan.request, state)


def value_of(model: FrameModel, kind: RowKind, label: str = "") -> float:
    rows = [r for r in model.rows if r.kind is kind and (not label or r.label == label)]
    assert rows, f"no {kind} row labelled {label!r}"
    return rows[0].value


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


# --- the expanded view: every thread has its own row --------------------------


def test_total_is_the_mean_of_the_thread_rows(hybrid_topology: Topology) -> None:
    model = build(hybrid_topology, 200, 60)
    threads = [r.value for r in model.rows if r.kind is RowKind.PROCESSOR]
    assert len(threads) == hybrid_topology.n_cpus
    assert value_of(model, RowKind.TOTAL) == pytest.approx(mean(threads))


def test_each_class_row_is_the_mean_of_its_own_threads(
    hybrid_topology: Topology,
) -> None:
    model = build(hybrid_topology, 200, 60)
    by_label = {r.label: r for r in model.rows if r.kind is RowKind.PROCESSOR}
    for klass in hybrid_topology.classes:
        members = [by_label[str(cpu.lp_id)].value for cpu in hybrid_topology.cpus_of_class(klass)]
        assert value_of(model, RowKind.CLASS, klass.value) == pytest.approx(mean(members))


def test_total_is_the_thread_weighted_mean_of_the_classes(
    hybrid_topology: Topology,
) -> None:
    """The classes have different thread counts, so a plain mean would be wrong."""
    model = build(hybrid_topology, 200, 60)
    weighted = sum(
        value_of(model, RowKind.CLASS, klass.value) * len(hybrid_topology.cpus_of_class(klass))
        for klass in hybrid_topology.classes
    )
    assert value_of(model, RowKind.TOTAL) == pytest.approx(weighted / hybrid_topology.n_cpus)


# --- the folded views ---------------------------------------------------------


@pytest.mark.parametrize("rows", [24, 20, 18, 16, 14])
def test_folded_rows_are_the_mean_of_the_cores_they_cover(
    hybrid_topology: Topology, rows: int
) -> None:
    """Whatever bucket size the layout picks, a row still means what it says."""
    history = HistoryStore(HISTORY_CAPACITY)
    collector = CpuCollector(FakeSampler(LOAD), hybrid_topology)
    history.ensure_many(collector.series_keys())
    history.push(collector.collect(), at=0.0)

    state = UiState()
    renderer = CliRenderer(hybrid_topology)
    plan = renderer.plan(Viewport(200, rows), state, MEMORY, history.marker_state())
    model = FrameBuilder(hybrid_topology, history).build(plan.request, state)

    group_rows = [r for r in model.rows if r.kind is RowKind.GROUP]
    if not group_rows:
        pytest.skip(f"{rows} rows does not fold")

    buckets = [
        cores
        for _klass, per_class in core_buckets(hybrid_topology, plan.geometry.group_size)
        for cores in per_class
    ]
    assert len(group_rows) == len(buckets)
    for row, cores in zip(group_rows, buckets, strict=True):
        members = [LOAD[cpu.index] for core in cores for cpu in core.cpus]
        assert row.value == pytest.approx(mean(members)), row.label


def test_the_total_survives_folding(hybrid_topology: Topology) -> None:
    """TOTAL is the mean of every thread, however few rows are on screen."""
    for rows in (60, 24, 18, 12, 9):
        model = build(hybrid_topology, 200, rows)
        assert value_of(model, RowKind.TOTAL) == pytest.approx(mean(LOAD)), rows


# --- what actually reaches the screen -----------------------------------------


def test_the_printed_aggregate_matches_the_printed_rows(
    hybrid_topology: Topology,
) -> None:
    """As printed, to one decimal, the arithmetic must still hold.

    Each row is rounded before it is shown, so a reader adding up the screen can
    be off by half a display step per value — and no more than that.
    """
    model = build(hybrid_topology, 200, 60)
    threads = [r.value for r in model.rows if r.kind is RowKind.PROCESSOR]

    printed_threads = [float(fmt_percent(v).rstrip("%")) for v in threads]
    printed_total = float(fmt_percent(value_of(model, RowKind.TOTAL)).rstrip("%"))
    assert printed_total == pytest.approx(mean(printed_threads), abs=0.1)


def test_a_uniform_machine_reports_the_same_value_everywhere(
    uniform_topology: Topology,
) -> None:
    history = HistoryStore(HISTORY_CAPACITY)
    collector = CpuCollector(FakeSampler([42.0] * uniform_topology.n_cpus), uniform_topology)
    history.ensure_many(collector.series_keys())
    history.push(collector.collect(), at=0.0)

    state = UiState()
    renderer = CliRenderer(uniform_topology)
    plan = renderer.plan(Viewport(200, 60), state, MEMORY, history.marker_state())
    model = FrameBuilder(uniform_topology, history).build(plan.request, state)

    cpu_rows = [r for r in model.rows if r.kind is not RowKind.MEMORY]
    assert cpu_rows
    for row in cpu_rows:
        assert row.value == pytest.approx(42.0), row.label


def test_a_single_core_machine_totals_to_itself() -> None:
    from cpumon.core.topology import build_topology

    topo = build_topology("one", [(CoreClass.P, [0])])
    model = build(topo, 200, 60)
    assert value_of(model, RowKind.TOTAL) == pytest.approx(LOAD[0])
    assert value_of(model, RowKind.PROCESSOR, "0") == pytest.approx(LOAD[0])


# --- memory -------------------------------------------------------------------


def test_the_memory_row_is_used_over_total() -> None:
    info = MemoryInfo(total=32 * 2**30, available=12 * 2**30)
    assert info.used == 20 * 2**30
    assert info.percent == pytest.approx(62.5)


def test_the_swap_row_is_used_over_swap_total() -> None:
    info = MemoryInfo(
        total=16 * 2**30,
        available=8 * 2**30,
        backing_kind="swap",
        backing_total=8 * 2**30,
        backing_used=2 * 2**30,
    )
    assert info.backing_percent == pytest.approx(25.0)
    # The two rows are independent: swap use is not a share of RAM.
    assert info.percent == pytest.approx(50.0)


def test_memory_series_carry_the_percentages_the_rows_show() -> None:
    from cpumon.core.collectors import MemoryCollector

    class Source:
        def read_memory(self) -> MemoryInfo:
            return MemoryInfo(
                total=1000,
                available=250,
                backing_kind="swap",
                backing_total=400,
                backing_used=100,
            )

    out = MemoryCollector(Source()).collect()  # type: ignore[arg-type]
    assert out[SeriesKey.MEMORY] == pytest.approx(75.0)
    assert out[SeriesKey.BACKING] == pytest.approx(25.0)

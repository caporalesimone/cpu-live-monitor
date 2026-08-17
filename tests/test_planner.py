"""Row planning: which rows a geometry shows, and which series feed them."""

from __future__ import annotations

from dataclasses import replace

from cpumon.core.history import SeriesKey
from cpumon.core.model import MemoryInfo, Topology
from cpumon.core.topology import core_buckets
from cpumon.ui.model import MetricKind, RowKind, ScreenKind
from cpumon.ui.renders.cli.layout import LayoutSolver, RowMode
from cpumon.ui.renders.cli.planner import RowPlanner
from tests.conftest import all_series_keys


def plan_rows(
    topo: Topology,
    cols: int,
    rows: int,
    memory: MemoryInfo | None = None,
    *,
    samples: int = 30,
):
    geom = LayoutSolver(topo, has_backing=bool(memory and memory.has_backing)).solve(cols, rows)
    request = RowPlanner(topo).request(geom, memory, samples)
    return geom, request


def test_the_request_is_a_dashboard_request(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    _geom, request = plan_rows(hybrid_topology, 200, 60, memory)
    assert request.screen is ScreenKind.DASHBOARD
    assert request.sample_count == 30
    assert request.span_key == SeriesKey.TOTAL


def test_per_cpu_layout_plans_one_row_per_thread(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    geom, request = plan_rows(hybrid_topology, 200, 60, memory)
    assert geom.row_mode is RowMode.PER_CPU
    body = [spec for spec in request.rows if spec.kind is RowKind.PROCESSOR]
    assert [spec.label for spec in body] == [str(cpu.lp_id) for cpu in hybrid_topology.cpus]
    assert [spec.detail for spec in body] == [cpu.type_tag for cpu in hybrid_topology.cpus]
    assert [spec.series_key for spec in body] == [
        SeriesKey.cpu(cpu.index) for cpu in hybrid_topology.cpus
    ]


def test_folded_layout_plans_one_row_per_bucket(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    geom, request = plan_rows(hybrid_topology, 200, 24, memory)
    assert geom.row_mode is RowMode.PER_GROUP
    body = [spec for spec in request.rows if spec.kind is RowKind.GROUP]
    expected = [
        bucket
        for _klass, buckets in core_buckets(hybrid_topology, geom.group_size)
        for bucket in buckets
    ]
    assert len(body) == len(expected)
    assert all(spec.series_key.startswith("cpu:") for spec in body)


def test_a_single_core_bucket_reuses_the_per_core_series(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    """Group size 1 must not invent a second series for the same data."""
    geom = LayoutSolver(hybrid_topology).solve(200, 60)
    folded = replace(geom, row_mode=RowMode.PER_GROUP, group_size=1)
    request = RowPlanner(hybrid_topology).request(folded, memory, 10)
    groups = [spec for spec in request.rows if spec.kind is RowKind.GROUP]
    assert [spec.series_key for spec in groups] == [
        SeriesKey.core(core.core_id) for core in hybrid_topology.cores
    ]


def test_aggregates_are_always_present(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    for rows in (60, 24, 12, 8):
        _geom, request = plan_rows(hybrid_topology, 200, rows, memory)
        kinds = [spec.kind for spec in request.rows]
        assert kinds[0] is RowKind.TOTAL
        assert RowKind.MEMORY in kinds


def test_class_rows_appear_only_on_a_hybrid_machine(
    hybrid_topology: Topology, uniform_topology: Topology, memory: MemoryInfo
) -> None:
    _geom, hybrid = plan_rows(hybrid_topology, 200, 60, memory)
    _geom2, uniform = plan_rows(uniform_topology, 200, 60, memory)
    assert [spec.label for spec in hybrid.rows if spec.kind is RowKind.CLASS] == [
        "P",
        "E",
    ]
    assert not [spec for spec in uniform.rows if spec.kind is RowKind.CLASS]


def test_the_total_row_counts_threads(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    _geom, request = plan_rows(hybrid_topology, 200, 60, memory)
    total = request.rows[0]
    assert total.detail == f"{hybrid_topology.n_cpus}T"
    assert total.metric is MetricKind.CPU


def test_memory_rows_carry_the_memory_metric(
    hybrid_topology: Topology, memory_with_swap: MemoryInfo
) -> None:
    _geom, request = plan_rows(hybrid_topology, 200, 60, memory_with_swap)
    memory_rows = [spec for spec in request.rows if spec.kind in (RowKind.MEMORY, RowKind.BACKING)]
    assert [spec.label for spec in memory_rows] == ["RAM", "SWAP"]
    assert all(spec.metric is MetricKind.MEMORY for spec in memory_rows)
    assert [spec.detail for spec in memory_rows] == ["16GB", "8GB"]


def test_no_swap_row_without_a_backing_store(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    _geom, request = plan_rows(hybrid_topology, 200, 60, memory)
    assert not [spec for spec in request.rows if spec.kind is RowKind.BACKING]


def test_unknown_memory_still_gets_a_row(hybrid_topology: Topology) -> None:
    _geom, request = plan_rows(hybrid_topology, 200, 60, None)
    ram = next(spec for spec in request.rows if spec.kind is RowKind.MEMORY)
    assert ram.detail == "--"


def test_every_planned_series_is_one_the_collectors_produce(
    hybrid_topology: Topology, memory_with_swap: MemoryInfo
) -> None:
    """A planned row with no series behind it would draw a flat zero."""
    known = set(all_series_keys(hybrid_topology))
    for rows in (60, 40, 24, 18, 12, 9, 8):
        _geom, request = plan_rows(hybrid_topology, 200, rows, memory_with_swap)
        for spec in request.rows:
            assert spec.series_key in known, f"{spec.label} -> {spec.series_key}"

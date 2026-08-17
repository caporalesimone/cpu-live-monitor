"""Collectors: the aggregation plan is precomputed, so check what it produces."""

from __future__ import annotations

import pytest

from cpumon.core.collectors import CpuCollector, MemoryCollector
from cpumon.core.history import HistoryStore, SeriesKey
from cpumon.core.model import CoreClass, MemoryInfo, Topology
from cpumon.core.topology import GROUP_SIZES, core_buckets
from cpumon.runtime.sampling import SamplerWorker
from cpumon.settings import HISTORY_CAPACITY
from tests.conftest import FakeSampler


def test_every_declared_key_is_produced(hybrid_topology: Topology) -> None:
    sampler = FakeSampler([10.0] * hybrid_topology.n_cpus)
    collector = CpuCollector(sampler, hybrid_topology)
    produced = collector.collect()
    assert set(collector.series_keys()) == set(produced)


def test_keys_are_unique(hybrid_topology: Topology) -> None:
    keys = CpuCollector(FakeSampler([0.0] * hybrid_topology.n_cpus), hybrid_topology).series_keys()
    assert len(keys) == len(set(keys))


def test_per_cpu_values_are_passed_straight_through(
    hybrid_topology: Topology,
) -> None:
    values = [float(i) for i in range(hybrid_topology.n_cpus)]
    out = CpuCollector(FakeSampler(values), hybrid_topology).collect()
    for cpu in hybrid_topology.cpus:
        assert out[SeriesKey.cpu(cpu.index)] == values[cpu.index]


def test_total_is_the_mean_of_the_threads(hybrid_topology: Topology) -> None:
    values = [float(i) for i in range(hybrid_topology.n_cpus)]
    out = CpuCollector(FakeSampler(values), hybrid_topology).collect()
    assert out[SeriesKey.TOTAL] == pytest.approx(sum(values) / len(values))


def test_core_and_class_aggregates_are_means(hybrid_topology: Topology) -> None:
    values = [float(i) for i in range(hybrid_topology.n_cpus)]
    out = CpuCollector(FakeSampler(values), hybrid_topology).collect()
    for core in hybrid_topology.cores:
        members = [values[c.index] for c in core.cpus]
        assert out[SeriesKey.core(core.core_id)] == pytest.approx(sum(members) / len(members))
    for klass in hybrid_topology.classes:
        members = [values[c.index] for c in hybrid_topology.cpus_of_class(klass)]
        assert out[SeriesKey.klass(klass)] == pytest.approx(sum(members) / len(members))


def test_folded_groups_are_maintained_for_every_layout(
    hybrid_topology: Topology,
) -> None:
    """Resizing the window must never find a row without history."""
    values = [float(i) for i in range(hybrid_topology.n_cpus)]
    out = CpuCollector(FakeSampler(values), hybrid_topology).collect()
    for size in GROUP_SIZES:
        if size == 1:
            continue
        for klass, buckets in core_buckets(hybrid_topology, size):
            for index, cores in enumerate(buckets):
                key = SeriesKey.group(size, klass.value, index)
                members = [values[c.index] for core in cores for c in core.cpus]
                assert out[key] == pytest.approx(sum(members) / len(members))


def test_uniform_values_aggregate_to_themselves(uniform_topology: Topology) -> None:
    out = CpuCollector(FakeSampler([42.0] * uniform_topology.n_cpus), uniform_topology).collect()
    assert all(value == pytest.approx(42.0) for value in out.values())


def test_single_core_machine(uniform_topology: Topology) -> None:
    from cpumon.core.topology import build_topology

    topo = build_topology("one", [(CoreClass.P, [0])])
    out = CpuCollector(FakeSampler([7.0]), topo).collect()
    assert out[SeriesKey.TOTAL] == 7.0
    assert out[SeriesKey.cpu(0)] == 7.0


class StubBackend:
    """Only the memory half of the platform API is exercised here."""

    name = "stub"

    def __init__(self, readings: list[MemoryInfo]) -> None:
        self._readings = readings
        self.calls = 0

    def read_memory(self) -> MemoryInfo:
        info = self._readings[min(self.calls, len(self._readings) - 1)]
        self.calls += 1
        return info


def test_memory_collector_primes_itself() -> None:
    backend = StubBackend([MemoryInfo(total=100, available=40)])
    collector = MemoryCollector(backend)  # type: ignore[arg-type]
    assert backend.calls == 1
    assert collector.latest.percent == pytest.approx(60.0)


def test_memory_collector_publishes_the_last_reading() -> None:
    backend = StubBackend(
        [MemoryInfo(total=100, available=40), MemoryInfo(total=100, available=10)]
    )
    collector = MemoryCollector(backend)  # type: ignore[arg-type]
    out = collector.collect()
    assert out == {SeriesKey.MEMORY: pytest.approx(90.0)}
    assert collector.latest.available == 10


def test_backing_series_only_when_the_platform_reports_one() -> None:
    swap = MemoryInfo(
        total=100, available=50, backing_kind="swap", backing_total=200, backing_used=50
    )
    collector = MemoryCollector(StubBackend([swap]))  # type: ignore[arg-type]
    out = collector.collect()
    assert out[SeriesKey.BACKING] == pytest.approx(25.0)


class BrokenCollector(CpuCollector):
    def collect(self) -> dict[str, float]:
        raise RuntimeError("the platform blinked")


def test_a_failing_collector_does_not_stop_the_others(
    hybrid_topology: Topology,
) -> None:
    history = HistoryStore(HISTORY_CAPACITY)
    good = CpuCollector(FakeSampler([5.0] * hybrid_topology.n_cpus), hybrid_topology)
    broken = BrokenCollector(FakeSampler([0.0] * hybrid_topology.n_cpus), hybrid_topology)
    worker = SamplerWorker([broken, good], history, lambda: 1.0, lambda: None)
    worker.collect_once()
    assert history.latest(SeriesKey.TOTAL) == pytest.approx(5.0)


def test_nothing_is_pushed_when_every_collector_fails(
    hybrid_topology: Topology,
) -> None:
    history = HistoryStore(HISTORY_CAPACITY)
    broken = BrokenCollector(FakeSampler([0.0] * hybrid_topology.n_cpus), hybrid_topology)
    worker = SamplerWorker([broken], history, lambda: 1.0, lambda: None)
    worker.collect_once()
    assert history.marker_state().count == 0

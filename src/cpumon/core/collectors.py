"""Metric collectors: the only writers of the history store.

A collector declares the series it owns and how often it wants to run,
expressed as a multiple of the base tick. Today both collectors run every
tick; the indirection is what will allow, say, memory to be sampled once a
second while the CPU runs at 100 ms.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol

from cpumon.core.history import SeriesKey
from cpumon.core.model import MemoryInfo, Topology
from cpumon.core.topology import GROUP_SIZES, core_buckets


class SampleSource(Protocol):
    """The one thing a CPU collector needs from a platform.

    Declared here, and structurally, so the domain layer states its own
    requirements instead of importing the platform layer to learn them. The real
    :class:`~cpumon.backend.base.CpuSampler` satisfies it without being named.
    """

    def sample(self) -> list[float]: ...


class MemorySource(Protocol):
    """The one thing a memory collector needs from a platform."""

    def read_memory(self) -> MemoryInfo: ...


class MetricCollector(ABC):
    """A source of named time-series values."""

    name: str = "collector"
    every_n_ticks: int = 1

    @abstractmethod
    def series_keys(self) -> list[str]:
        """Every key this collector may produce, for pre-registration."""

    @abstractmethod
    def collect(self) -> dict[str, float]:
        """One reading, as {series key: value}."""


class CpuCollector(MetricCollector):
    """Per-logical-processor load, plus every aggregate the layout may show."""

    name = "cpu"

    def __init__(self, sampler: SampleSource, topology: Topology) -> None:
        self._sampler = sampler
        self._cpu_keys = [SeriesKey.cpu(c.index) for c in topology.cpus]
        # Aggregation plan, precomputed so the hot loop only sums.
        self._plan: list[tuple[str, tuple[int, ...]]] = [
            (SeriesKey.core(core.core_id), tuple(c.index for c in core.cpus))
            for core in topology.cores
        ]
        self._plan += [
            (
                SeriesKey.klass(klass),
                tuple(c.index for c in topology.cpus_of_class(klass)),
            )
            for klass in topology.classes
        ]
        # Every folding level the layout may ask for is maintained at all
        # times, so resizing the window never discards a row's history.
        for size in GROUP_SIZES:
            if size == 1:
                continue  # identical to the per-core series above
            for klass, buckets in core_buckets(topology, size):
                for i, cores in enumerate(buckets):
                    members = tuple(c.index for core in cores for c in core.cpus)
                    self._plan.append((SeriesKey.group(size, klass.value, i), members))

    def series_keys(self) -> list[str]:
        return [SeriesKey.TOTAL, *self._cpu_keys, *(key for key, _ in self._plan)]

    def collect(self) -> dict[str, float]:
        values = self._sampler.sample()
        out: dict[str, float] = {
            key: (values[i] if i < len(values) else 0.0) for i, key in enumerate(self._cpu_keys)
        }
        for key, members in self._plan:
            out[key] = sum(values[i] for i in members) / len(members)
        out[SeriesKey.TOTAL] = sum(values) / len(values) if values else 0.0
        return out


class MemoryCollector(MetricCollector):
    """Physical memory, plus the platform's backing-store metric if any.

    The last reading is kept on the collector rather than pushed into UI
    state: the view layer asks for it when it builds a frame, so the sampling
    thread and the render loop share nothing but this one reference.
    """

    name = "memory"

    def __init__(self, backend: MemorySource) -> None:
        self._backend = backend
        self._latest: MemoryInfo = backend.read_memory()

    @property
    def latest(self) -> MemoryInfo:
        """Most recent reading. Never None: one is taken at construction."""
        return self._latest

    def series_keys(self) -> list[str]:
        return [SeriesKey.MEMORY, SeriesKey.BACKING]

    def collect(self) -> dict[str, float]:
        info = self._backend.read_memory()
        self._latest = info
        out = {SeriesKey.MEMORY: info.percent}
        if info.has_backing:
            out[SeriesKey.BACKING] = info.backing_percent
        return out

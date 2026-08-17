"""One assembled monitor: topology, sampler, history, frame builder.

The interactive app and the diagnostic commands both need the same wiring, so it
exists once here. A session performs no I/O of its own beyond building the
pieces, and it knows nothing about how frames look: the renderer is chosen by
the caller and fed from the same pieces.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cpumon.backend.base import CpuSampler, PlatformBackend
from cpumon.core.collectors import CpuCollector, MemoryCollector, MetricCollector
from cpumon.core.errors import PlatformError
from cpumon.core.history import HistoryStore, MarkerState
from cpumon.core.model import MemoryInfo, Topology
from cpumon.runtime.sampling import SamplerWorker
from cpumon.settings import HISTORY_CAPACITY, clamp_interval
from cpumon.ui.builder import FrameBuilder
from cpumon.ui.model import FrameModel, FrameRequest
from cpumon.ui.state import UiState


@dataclass(frozen=True)
class MonitorSession:
    """Everything needed to produce frame models for one machine."""

    backend: PlatformBackend
    topology: Topology
    sampler: CpuSampler
    memory: MemoryCollector
    collectors: tuple[MetricCollector, ...]
    history: HistoryStore
    state: UiState
    builder: FrameBuilder

    @classmethod
    def create(cls, backend: PlatformBackend, interval: float) -> MonitorSession:
        topology = backend.read_topology()
        sampler = backend.create_sampler()
        _validate(topology, sampler)

        memory = MemoryCollector(backend)
        collectors: tuple[MetricCollector, ...] = (
            CpuCollector(sampler, topology),
            memory,
        )
        history = HistoryStore(HISTORY_CAPACITY)
        for collector in collectors:
            history.ensure_many(collector.series_keys())

        return cls(
            backend=backend,
            topology=topology,
            sampler=sampler,
            memory=memory,
            collectors=collectors,
            history=history,
            state=UiState(interval=clamp_interval(interval)),
            builder=FrameBuilder(topology, history),
        )

    # -- what a renderer needs to know about the machine ----------------------

    @property
    def has_backing(self) -> bool:
        """Whether this platform reports a backing store worth a screen row."""
        return self.memory.latest.has_backing

    @property
    def memory_info(self) -> MemoryInfo:
        return self.memory.latest

    def markers(self) -> MarkerState:
        return self.history.marker_state()

    def build(self, request: FrameRequest) -> FrameModel:
        return self.builder.build(request, self.state)

    # -- driving -------------------------------------------------------------

    def worker(self, on_sample: Callable[[], None]) -> SamplerWorker:
        """A sampler thread bound to this session's collectors and cadence."""
        return SamplerWorker(
            self.collectors,
            self.history,
            lambda: self.state.interval,
            on_sample,
        )

    def refresh_uptime(self) -> None:
        self.state.uptime = self.backend.uptime_seconds()


def _validate(topology: Topology, sampler: CpuSampler) -> None:
    """The sampler indexes by position; the model must match it exactly."""
    n_model = topology.n_cpus
    n_sampler = sampler.count()
    if n_model != n_sampler:
        raise PlatformError(
            f"topology reports {n_model} logical processors but the sampler "
            f"reports {n_sampler}; refusing to display data that could be "
            "attributed to the wrong core"
        )
    for expected, cpu in enumerate(topology.cpus):
        if cpu.index != expected:
            raise PlatformError(f"non-contiguous processor indices at position {expected}")

"""Fills a renderer's request with data. The only reader of the history store.

This is the whole of the coupling between collection and display: a request
comes in naming series and a sample count, a model goes out carrying numbers.
The builder never learns what the frame will look like, and the renderer never
learns where the numbers came from.
"""

from __future__ import annotations

from cpumon.core.history import HistoryReading, HistoryStore
from cpumon.core.model import Topology
from cpumon.ui.model import (
    FrameModel,
    FrameRequest,
    MachineModel,
    RowModel,
    RowSpec,
)
from cpumon.ui.state import UiState


class FrameBuilder:
    """Turns a :class:`FrameRequest` into a :class:`FrameModel`."""

    def __init__(self, topology: Topology, history: HistoryStore) -> None:
        self._machine = MachineModel(
            name=topology.model_name,
            cores=topology.n_cores,
            threads=topology.n_cpus,
            hybrid=topology.hybrid,
        )
        self._history = history

    @property
    def machine(self) -> MachineModel:
        return self._machine

    def build(self, request: FrameRequest, state: UiState) -> FrameModel:
        # One read for the whole frame. Reading series one at a time would let a
        # sample land in the middle, and an aggregate taken before it would no
        # longer be the mean of the rows taken after it.
        reading = self._history.read(
            (spec.series_key for spec in request.rows),
            request.sample_count,
            request.span_key,
        )
        return FrameModel(
            machine=self._machine,
            interval=state.interval,
            uptime=state.uptime,
            rows=tuple(self._row(spec, reading) for spec in request.rows),
            history_span=reading.span,
        )

    @staticmethod
    def _row(spec: RowSpec, reading: HistoryReading) -> RowModel:
        series = reading.of(spec.series_key)
        return RowModel(spec=spec, value=series.latest, samples=series.samples)

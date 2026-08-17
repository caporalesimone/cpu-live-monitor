"""Which rows this frame shows, and which series feed them.

Row planning belongs to the renderer: how much detail fits is a layout question,
and only the layout knows how wide the label column is. What leaves this module
is a list of :class:`~cpumon.ui.model.RowSpec` — identity, not appearance — so
the data layer can fetch values without seeing a single column width.
"""

from __future__ import annotations

from cpumon.core.history import SeriesKey
from cpumon.core.model import CoreClass, MemoryInfo, PhysicalCore, Topology
from cpumon.core.topology import bucket_label, core_buckets
from cpumon.ui.model import (
    FrameRequest,
    MetricKind,
    RowKind,
    RowSpec,
    ScreenKind,
)
from cpumon.ui.renders.cli.formatting import capacity_label
from cpumon.ui.renders.cli.layout import Geometry, RowMode

_UNKNOWN_CAPACITY = "--"


class RowPlanner:
    """Builds the row list for one geometry."""

    def __init__(self, topology: Topology) -> None:
        self._topo = topology

    def request(self, geom: Geometry, memory: MemoryInfo | None, sample_count: int) -> FrameRequest:
        """The full data request for a dashboard frame."""
        rows = (
            *self._aggregates(geom),
            *self._body(geom),
            self._memory(memory),
            *self._backing(geom, memory),
        )
        return FrameRequest(
            screen=ScreenKind.DASHBOARD,
            rows=rows,
            sample_count=sample_count,
            span_key=SeriesKey.TOTAL,
        )

    # -- row groups ----------------------------------------------------------

    def _aggregates(self, geom: Geometry) -> tuple[RowSpec, ...]:
        total = RowSpec(
            kind=RowKind.TOTAL,
            label="TOTAL",
            detail=f"{self._topo.n_cpus}T",
            series_key=SeriesKey.TOTAL,
        )
        if geom.summary_rows <= 1:
            return (total,)
        return (
            total,
            *(
                RowSpec(
                    kind=RowKind.CLASS,
                    label=klass.value,
                    detail=f"{len(self._topo.cpus_of_class(klass))}T",
                    series_key=SeriesKey.klass(klass),
                )
                for klass in self._topo.classes
            ),
        )

    def _body(self, geom: Geometry) -> tuple[RowSpec, ...]:
        if geom.row_mode is RowMode.PER_CPU:
            return tuple(
                RowSpec(
                    kind=RowKind.PROCESSOR,
                    label=str(cpu.lp_id),
                    detail=cpu.type_tag,
                    series_key=SeriesKey.cpu(cpu.index),
                )
                for cpu in self._topo.cpus
            )
        if geom.row_mode is RowMode.PER_GROUP:
            return tuple(
                RowSpec(
                    kind=RowKind.GROUP,
                    label=bucket_label(cores),
                    detail=klass.value,
                    series_key=self._group_key(geom.group_size, klass, index, cores[0]),
                )
                for klass, buckets in core_buckets(self._topo, geom.group_size)
                for index, cores in enumerate(buckets)
            )
        return ()

    @staticmethod
    def _group_key(group_size: int, klass: CoreClass, index: int, first: PhysicalCore) -> str:
        """Per core at size 1, per bucket above it."""
        if group_size == 1:
            return SeriesKey.core(first.core_id)
        return SeriesKey.group(group_size, klass.value, index)

    @staticmethod
    def _memory(memory: MemoryInfo | None) -> RowSpec:
        known = memory is not None and memory.total > 0
        return RowSpec(
            kind=RowKind.MEMORY,
            label="RAM",
            detail=capacity_label(memory.total) if known and memory else _UNKNOWN_CAPACITY,
            series_key=SeriesKey.MEMORY,
            metric=MetricKind.MEMORY,
        )

    @staticmethod
    def _backing(geom: Geometry, memory: MemoryInfo | None) -> tuple[RowSpec, ...]:
        if not geom.show_backing or memory is None or not memory.has_backing:
            return ()
        return (
            RowSpec(
                kind=RowKind.BACKING,
                label=MemoryInfo.BACKING_ROW_LABEL,
                detail=capacity_label(memory.backing_total),
                series_key=SeriesKey.BACKING,
                metric=MetricKind.MEMORY,
            ),
        )

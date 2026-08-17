"""The renderer-agnostic description of one frame.

Nothing here knows about characters, colours, escape sequences or cells. A row
says *what* it is and *what it measures*; how that turns into pixels or glyphs
is the renderer's business alone. Two renderers can therefore disagree about
everything visual and still consume the same model.

The flow around these types is deliberately three-legged:

    renderer.plan(...)  -> RenderPlan, whose .request says which series and how
                           many samples the frame needs
    builder.build(...)  -> FrameModel, values only, read from the history store
    renderer.render(...) -> the frame

so the layer that reads data never learns how it will be drawn, and the layer
that draws never touches the data store.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class MetricKind(Enum):
    """What a row measures. Renderers map this onto a colour scale."""

    CPU = auto()
    MEMORY = auto()


class RowKind(Enum):
    """What a row *is*, which is what decides how it is emphasised.

    Presentation follows from this and nothing else: a renderer picks bold for
    the aggregates, a core-class colour for the per-processor type tags, and
    spells the cadence marker out on the TOTAL row only.
    """

    TOTAL = auto()  # every thread, aggregated
    CLASS = auto()  # one performance class (P / E / LPE)
    PROCESSOR = auto()  # one logical processor
    GROUP = auto()  # one or more physical cores, folded together
    MEMORY = auto()  # physical memory
    BACKING = auto()  # the platform's backing store (swap)

    @property
    def is_aggregate(self) -> bool:
        return self in (RowKind.TOTAL, RowKind.MEMORY, RowKind.BACKING)

    @property
    def shows_core_class(self) -> bool:
        """True when the detail text is a core-class tag, not a count or size."""
        return self in (RowKind.PROCESSOR, RowKind.GROUP)


class ScreenKind(Enum):
    """Which screen a frame is."""

    DASHBOARD = auto()
    HELP = auto()
    UNAVAILABLE = auto()  # the viewport cannot hold a readable frame


@dataclass(frozen=True)
class Viewport:
    """The space a renderer has to fill, in whatever units it works in.

    For a terminal these are columns and rows of characters.
    """

    cols: int
    rows: int


@dataclass(frozen=True)
class RowSpec:
    """A row the renderer intends to draw, and the series that feeds it."""

    kind: RowKind
    label: str  # identity: "TOTAL", "P", "7", "0-5", "RAM", "SWAP"
    detail: str  # secondary text: "20T", "PHT", "32GB"
    series_key: str
    metric: MetricKind = MetricKind.CPU


@dataclass(frozen=True)
class FrameRequest:
    """What the data layer must fetch for one frame.

    Produced by the renderer, consumed by the builder. An empty request is
    legitimate and cheap: the help page and an unusable viewport need no data.
    """

    screen: ScreenKind
    rows: tuple[RowSpec, ...] = ()
    sample_count: int = 0
    span_key: str = ""  # series whose measured duration labels the time axis

    @property
    def needs_history(self) -> bool:
        return bool(self.rows)


@dataclass(frozen=True)
class RowModel:
    """A row with its numbers filled in."""

    spec: RowSpec
    value: float
    samples: tuple[float, ...] = ()

    @property
    def kind(self) -> RowKind:
        return self.spec.kind

    @property
    def label(self) -> str:
        return self.spec.label

    @property
    def detail(self) -> str:
        return self.spec.detail

    @property
    def metric(self) -> MetricKind:
        return self.spec.metric


@dataclass(frozen=True)
class MachineModel:
    """The machine being watched, as facts rather than as a caption."""

    name: str
    cores: int
    threads: int
    hybrid: bool


@dataclass(frozen=True)
class FrameModel:
    """Everything one frame shows, as values.

    ``history_span`` is the wall-clock duration actually covered by the samples
    in the rows — measured from their timestamps, never inferred from the
    interval, which is what lets a renderer label the axis honestly.
    """

    machine: MachineModel
    interval: float
    uptime: float
    rows: tuple[RowModel, ...] = ()
    history_span: float = 0.0

    def rows_of_kind(self, *kinds: RowKind) -> tuple[RowModel, ...]:
        return tuple(row for row in self.rows if row.kind in kinds)

    def first_of_kind(self, kind: RowKind) -> RowModel | None:
        return next((row for row in self.rows if row.kind is kind), None)

"""Responsive geometry solver.

A pure function of (cols, rows, topology) -> :class:`Geometry`. No I/O and no
state, so it is trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from cpumon.core.model import Topology
from cpumon.core.topology import GROUP_SIZES


class RowMode(Enum):
    """How much vertical detail fits."""

    PER_CPU = "per-cpu"  # one row per logical processor
    PER_GROUP = "per-group"  # cores folded into buckets of N (N=1 -> per core)
    PER_CLASS = "per-class"  # one row per performance class
    TOTAL_ONLY = "total-only"  # aggregates only
    TOO_SHORT = "too-short"


class ColMode(Enum):
    """How much horizontal detail fits."""

    FULL = "full"  # cpu | type | usage | gauge | history
    NO_HISTORY = "no-history"  # cpu | type | usage | gauge
    NO_GAUGE = "no-gauge"  # cpu | type | usage
    NO_TYPE = "no-type"  # cpu | usage
    TOO_NARROW = "too-narrow"


# Column widths. The whole horizontal geometry derives from these.
W_CPU: Final = 5
W_TYPE: Final = 4
W_USAGE: Final = 5  # "99.9%" or "100%" — see formatting.fmt_percent
W_GAUGE: Final = 10
SEP_W: Final = 3  # " | "
MIN_HISTORY: Final = 10
MAX_HISTORY: Final = 400

# Line width per column mode: a leading space, then each column preceded by its
# separator. The values are spelled out in the comments because the "too small"
# screen quotes the narrowest one back to the user.
W_CPU_USAGE: Final = 1 + W_CPU + SEP_W + W_USAGE  # 14
W_WITH_TYPE: Final = 1 + W_CPU + SEP_W + W_TYPE + SEP_W + W_USAGE  # 21
W_WITH_GAUGE: Final = W_WITH_TYPE + SEP_W + W_GAUGE  # 34
W_WITH_HISTORY: Final = W_WITH_GAUGE + SEP_W  # 37 + history

# Vertical chrome, counted row by row so the solver and the renderer cannot
# drift apart:
#   title, subtitle, rule, column head, head separator,
#   [aggregates], [separator + core rows], separator, RAM, rule, footer
CHROME_WITH_TABLE: Final = 10  # includes the separator before the core rows
CHROME_NO_TABLE: Final = 9  # aggregates and RAM only, still under a column head
CHROME_MINIMAL: Final = 7  # title, subtitle, rule, TOTAL, RAM, rule, footer


@dataclass(frozen=True)
class Geometry:
    """The shape of one frame."""

    cols: int
    rows: int
    col_mode: ColMode
    row_mode: RowMode
    history_width: int
    line_width: int
    summary_rows: int
    body_rows: int
    group_size: int = 1
    show_backing: bool = False  # SWAP row under RAM

    @property
    def show_type(self) -> bool:
        return self.col_mode in (ColMode.FULL, ColMode.NO_HISTORY, ColMode.NO_GAUGE)

    @property
    def show_gauge(self) -> bool:
        return self.col_mode in (ColMode.FULL, ColMode.NO_HISTORY)

    @property
    def show_history(self) -> bool:
        return self.col_mode is ColMode.FULL

    @property
    def show_table(self) -> bool:
        return self.row_mode in (RowMode.PER_CPU, RowMode.PER_GROUP)

    @property
    def minimal(self) -> bool:
        """True when only the aggregates and memory are drawn, with no head."""
        return self.row_mode is RowMode.TOTAL_ONLY

    @property
    def usable(self) -> bool:
        return self.col_mode is not ColMode.TOO_NARROW and self.row_mode is not RowMode.TOO_SHORT


class LayoutSolver:
    """Turns a terminal size into a :class:`Geometry` for one topology."""

    def __init__(self, topology: Topology, *, has_backing: bool = False) -> None:
        self._topo = topology
        self._n_classes = len(topology.classes) if topology.hybrid else 0
        # The SWAP row costs one row in every mode. Whether it exists is a
        # property of the machine, so it is fixed at construction and both the
        # solver and the renderer read it from the Geometry.
        self._has_backing = has_backing
        self._extra = 1 if has_backing else 0
        # Cores per class, used to size the folded views.
        self._class_sizes: tuple[int, ...] = tuple(
            len(topology.cores_of_class(k)) for k in topology.classes
        )

    # -- public --------------------------------------------------------------

    def solve(self, cols: int, rows: int) -> Geometry:
        col_mode, history_width, line_width = self._solve_columns(cols)
        row_mode, summary_rows, body_rows, group = self._solve_rows(rows)
        return Geometry(
            cols=cols,
            rows=rows,
            col_mode=col_mode,
            row_mode=row_mode,
            history_width=history_width,
            line_width=line_width,
            summary_rows=summary_rows,
            body_rows=body_rows,
            group_size=group,
            show_backing=self._has_backing,
        )

    def rows_for_group(self, group: int) -> int:
        """Body rows produced by folding each class into buckets of *group*."""
        return sum(-(-n // group) for n in self._class_sizes)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _solve_columns(cols: int) -> tuple[ColMode, int, int]:
        if cols >= W_WITH_HISTORY + MIN_HISTORY:
            width = min(MAX_HISTORY, cols - W_WITH_HISTORY)
            return ColMode.FULL, width, W_WITH_HISTORY + width
        if cols >= W_WITH_GAUGE:
            return ColMode.NO_HISTORY, 0, W_WITH_GAUGE
        if cols >= W_WITH_TYPE:
            return ColMode.NO_GAUGE, 0, W_WITH_TYPE
        if cols >= W_CPU_USAGE:
            return ColMode.NO_TYPE, 0, W_CPU_USAGE
        return ColMode.TOO_NARROW, 0, cols

    def _solve_rows(self, rows: int) -> tuple[RowMode, int, int, int]:
        # Aggregate block: TOTAL plus one row per performance class. Memory is
        # not part of it — it sits on its own below the CPU rows.
        aggregates = 1 + self._n_classes
        overhead = CHROME_WITH_TABLE + self._extra + aggregates

        if rows >= overhead + self._topo.n_cpus:
            return RowMode.PER_CPU, aggregates, self._topo.n_cpus, 1

        # Fold cores into progressively larger buckets before giving up on the
        # table. Each step still shows more detail than the per-class view.
        for group in GROUP_SIZES:
            body = self.rows_for_group(group)
            if body <= self._n_classes:
                break  # folding further just reproduces the per-class view
            if rows >= overhead + body:
                return RowMode.PER_GROUP, aggregates, body, group

        if rows >= CHROME_NO_TABLE + self._extra + aggregates and self._n_classes:
            return RowMode.PER_CLASS, aggregates, 0, 1
        if rows >= CHROME_MINIMAL + self._extra:
            return RowMode.TOTAL_ONLY, 1, 0, 1
        return RowMode.TOO_SHORT, 0, 0, 1

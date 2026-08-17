"""Frame composition for a character-cell terminal.

A model in, one string out. The renderer decides everything visual — which
columns fit, what is bold, which colour a value gets — from the row *kinds* and
*metrics* in the model, and reads no data of its own. The caller performs
exactly one write per frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from cpumon.app_info import APP_AUTHOR, APP_NAME, APP_NAME_SHORT, APP_VERSION, APP_YEAR
from cpumon.core.history import MarkerState
from cpumon.core.model import MemoryInfo, Topology
from cpumon.ui.help import HelpContent
from cpumon.ui.model import (
    FrameModel,
    FrameRequest,
    RowKind,
    RowModel,
    ScreenKind,
    Viewport,
)
from cpumon.ui.renderer import Renderer, RenderPlan
from cpumon.ui.renders.cli import ansi
from cpumon.ui.renders.cli.clock import Clock
from cpumon.ui.renders.cli.formatting import fmt_duration, fmt_percent, fmt_window
from cpumon.ui.renders.cli.glyphs import Glyph
from cpumon.ui.renders.cli.helpstyle import StyledLine, style_help
from cpumon.ui.renders.cli.layout import (
    CHROME_MINIMAL,
    W_CPU,
    W_CPU_USAGE,
    W_GAUGE,
    W_TYPE,
    W_USAGE,
    Geometry,
    LayoutSolver,
)
from cpumon.ui.renders.cli.palette import palette_for
from cpumon.ui.renders.cli.planner import RowPlanner
from cpumon.ui.renders.cli.theme import Theme
from cpumon.ui.renders.cli.trend import TrendPlan, build_trend_plan
from cpumon.ui.renders.cli.widgets import GAUGE, GAUGE_SCALE_LABEL, SPARK
from cpumon.ui.state import UiState

# The credit line degrades from the back: copyright, then author, then the
# version, then the name itself shortens.
_TITLE_VARIANTS: Final[tuple[tuple[str, str], ...]] = (
    (APP_NAME, f"v{APP_VERSION} - {APP_AUTHOR} - {Glyph.COPY} {APP_YEAR}"),
    (APP_NAME, f"v{APP_VERSION} - {APP_AUTHOR}"),
    (APP_NAME, f"v{APP_VERSION}"),
    (APP_NAME_SHORT, f"v{APP_VERSION}"),
    (APP_NAME_SHORT, ""),
)

_FOOTER_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("F1", "Help"),
    ("F2", "Faster"),
    ("F3", "Slower"),
    ("q", "Quit"),
)

_HELP_HINTS_SCROLLABLE: Final[tuple[tuple[str, str], ...]] = (
    ("Up/Dn", "Scroll "),
    ("PgUp/PgDn", "Page "),
    ("Esc", "Close "),
)
_HELP_HINTS_STATIC: Final[tuple[tuple[str, str], ...]] = (("Esc", "Close "),)

_MIN_COLS_FOR_LONG_WARNING: Final = 16


def _title_variant(width: int) -> tuple[str, str, int]:
    """Widest title that leaves two columns spare, with its printed length."""
    for name, trail in _TITLE_VARIANTS:
        length = 1 + len(name) + (1 + len(trail) if trail else 0)
        if length + 2 <= width:
            return name, trail, length
    name, trail = _TITLE_VARIANTS[-1]
    return name, trail, 1 + len(name)


def _key_hint(num: str, label: str) -> tuple[str, int]:
    """A styled key hint and the number of columns it occupies."""
    return (
        Theme.KEY_NUM + num + Theme.RESET + Theme.KEY_LABEL + label + Theme.RESET,
        len(num) + len(label),
    )


def _hint_bar(hints: tuple[tuple[str, str], ...], width: int) -> tuple[str, int]:
    """Key bar built from whole segments only, plus the columns it used.

    Styled text must never be sliced: a cut inside an escape sequence leaks raw
    bytes onto the screen. Segments are therefore added while they fit and
    dropped entirely otherwise.
    """
    parts: list[str] = []
    used = 1  # leading space
    for num, label in hints:
        styled, size = _key_hint(num, label)
        if used + size > width:
            break
        parts.append(styled)
        used += size
    return " " + "".join(parts), used


@dataclass(frozen=True)
class CliPlan(RenderPlan):
    """What this frame will look like, decided before any data is fetched."""

    geometry: Geometry
    trend: TrendPlan
    help_scroll: int
    _request: FrameRequest

    @property
    def request(self) -> FrameRequest:
        return self._request

    @property
    def screen(self) -> ScreenKind:
        return self._request.screen


class CliRenderer(Renderer):
    """Composes complete frames for an ANSI terminal."""

    CLOCK_ROW = 0

    def __init__(
        self,
        topology: Topology,
        *,
        has_backing: bool = False,
        clock: Clock | None = None,
        help_content: HelpContent | None = None,
    ) -> None:
        self._layout = LayoutSolver(topology, has_backing=has_backing)
        self._planner = RowPlanner(topology)
        self._clock = clock if clock is not None else Clock()
        self._help = help_content if help_content is not None else HelpContent()
        self._help_lines: tuple[StyledLine, ...] = tuple(
            style_help(line) for line in self._help.lines()
        )
        self._clock_col = 0

    # -- planning ------------------------------------------------------------

    def plan(
        self,
        viewport: Viewport,
        state: UiState,
        memory: MemoryInfo | None,
        markers: MarkerState,
    ) -> CliPlan:
        geom = self._layout.solve(viewport.cols, viewport.rows)
        if not geom.usable:
            return CliPlan(geom, TrendPlan.empty(), 0, FrameRequest(ScreenKind.UNAVAILABLE))
        if state.help_visible:
            return CliPlan(
                geom,
                TrendPlan.empty(),
                self._help.clamp_scroll(state.help_scroll, geom.rows),
                FrameRequest(ScreenKind.HELP),
            )
        # One trend plan per frame, shared by every row: were the rows to differ,
        # the time axis would differ between them and the seams would not line
        # up. Markers take cells of their own, so they decide how many samples
        # the frame can hold.
        trend = (
            build_trend_plan(geom.history_width, markers)
            if geom.show_history
            else TrendPlan.empty()
        )
        request = self._planner.request(geom, memory, trend.samples)
        return CliPlan(geom, trend, 0, request)

    # -- rendering -----------------------------------------------------------

    def render(self, plan: RenderPlan, model: FrameModel) -> str:
        if not isinstance(plan, CliPlan):  # pragma: no cover - defensive
            raise TypeError("CliRenderer requires a plan of its own making")
        if plan.screen is ScreenKind.UNAVAILABLE:
            self._clock_col = 0
            return self._too_small(plan.geometry)
        if plan.screen is ScreenKind.HELP:
            self._clock_col = 0
            return self._help_screen(plan.geometry, plan.help_scroll)
        return self._dashboard(plan, model)

    def begin(self) -> str:
        """Hide the cursor: it would blink wherever the last write landed."""
        return ansi.HIDE_CURSOR

    def end(self) -> str:
        """Leave a clean prompt.

        Drop any lingering attribute, wipe the screen, home the cursor, then make
        it visible again. Doing it in this order means a terminal that ignores
        one sequence still ends up in a sane state.
        """
        return Theme.RESET + ansi.CLEAR_SCREEN + ansi.HOME + ansi.SHOW_CURSOR

    def partial_update(self) -> str:
        """Cheap repaint: the clock alone, without touching the rest."""
        if self._clock_col <= 0:
            return ""
        return (
            ansi.move(self.CLOCK_ROW, self._clock_col)
            + Theme.CLOCK
            + self._clock.text()
            + Theme.RESET
        )

    # -- screens -------------------------------------------------------------

    @staticmethod
    def _too_small(geom: Geometry) -> str:
        msg = "window too small" if geom.cols >= _MIN_COLS_FOR_LONG_WARNING else "too small"
        lines = (msg, f"{geom.cols}x{geom.rows}", f"min {W_CPU_USAGE}x{CHROME_MINIMAL}")
        out = [ansi.CLEAR_SCREEN]
        row = max(0, geom.rows // 2 - 1)
        for offset, raw in enumerate(lines):
            if row + offset >= geom.rows:
                break
            text = raw[: geom.cols]
            pad = max(0, (geom.cols - len(text)) // 2)
            colour = Theme.WARNING if offset == 0 else Theme.SUBTITLE
            out.append(ansi.at(row + offset) + " " * pad + colour + text + Theme.RESET)
        return "".join(out)

    def _help_screen(self, geom: Geometry, scroll: int) -> str:
        viewport = self._help.viewport(geom.rows)
        top = self._help.clamp_scroll(scroll, geom.rows)
        total = len(self._help_lines)

        out = [ansi.CLEAR_SCREEN]
        for i, (plain, styled) in enumerate(self._help_lines[top : top + viewport]):
            # Styled text is emitted whole or not at all: slicing it would cut an
            # escape sequence in half.
            body = styled if len(plain) + 1 <= geom.cols else plain[: geom.cols - 1]
            out.append(ansi.at(i) + " " + body)
        out.append(ansi.at(geom.rows - 1) + self._help_status(geom, top, viewport, total))
        return "".join(out)

    @staticmethod
    def _help_status(geom: Geometry, top: int, viewport: int, total: int) -> str:
        scrollable = total > viewport
        hints = _HELP_HINTS_SCROLLABLE if scrollable else _HELP_HINTS_STATIC
        left, used = _hint_bar(hints, geom.cols)
        if not scrollable:
            return left

        shown_to = min(top + viewport, total)
        right_text = f"{top + 1}-{shown_to} of {total}"
        if used + len(right_text) + 2 > geom.cols:
            return left
        gap = geom.cols - used - len(right_text) - 1
        return left + " " * gap + Theme.FOOTER_INFO + right_text + Theme.RESET

    def _dashboard(self, plan: CliPlan, model: FrameModel) -> str:
        geom = plan.geometry
        out: list[str] = [ansi.CLEAR_SCREEN]
        row = 0

        out.append(ansi.at(row) + self._title(geom))
        row += 1
        out.append(ansi.at(row) + self._subtitle(geom, model))
        row += 1
        out.append(ansi.at(row) + self._column_rule(geom, Glyph.TEE_DOWN))
        row += 1

        # One table: aggregates, then cores, then memory.
        #
        # The CPU aggregates belong to the same grid as the cores they summarise,
        # separated by a rule rather than living in a block of their own. Memory
        # is a different kind of quantity, so it sits below the CPU rows behind
        # its own rule instead of between them.
        if not geom.minimal:
            out.append(ansi.at(row) + self._column_head(geom, plan, model))
            row += 1
            out.append(ansi.at(row) + self._column_rule(geom))
            row += 1

        for aggregate in model.rows_of_kind(RowKind.TOTAL, RowKind.CLASS):
            out.append(ansi.at(row) + self._row(aggregate, geom, plan))
            row += 1

        body = model.rows_of_kind(RowKind.PROCESSOR, RowKind.GROUP)
        if geom.show_table:
            out.append(ansi.at(row) + self._column_rule(geom))
            row += 1
            for body_row in body:
                out.append(ansi.at(row) + self._row(body_row, geom, plan))
                row += 1

        if not geom.minimal:
            out.append(ansi.at(row) + self._column_rule(geom))
            row += 1
        memory = model.first_of_kind(RowKind.MEMORY)
        if memory is not None:
            out.append(ansi.at(row) + self._row(memory, geom, plan))
            row += 1
        backing = model.first_of_kind(RowKind.BACKING)
        if backing is not None and row < geom.rows - 1:
            out.append(ansi.at(row) + self._row(backing, geom, plan))
            row += 1

        # Close the table, then pin the key bar to the last row. The rule sits
        # immediately under the memory row so the table reads as closed, while
        # the key bar stays at the bottom edge: its position is what tells the
        # eye where the window actually ends.
        out.append(ansi.at(row) + self._column_rule(geom, Glyph.TEE_UP))
        out.append(ansi.at(geom.rows - 1) + self._footer(geom))
        return "".join(out)

    # -- pieces --------------------------------------------------------------

    def _title(self, geom: Geometry) -> str:
        """Application name and credits on the left, wall clock on the right."""
        width = geom.line_width
        name, trail, left_len = _title_variant(width)
        left = f" {Theme.TITLE}{name}{Theme.RESET}"
        if trail:
            left += f" {Theme.VERSION}{trail}{Theme.RESET}"

        clock = self._clock.text(max(0, width - left_len - 2))
        if not clock:
            self._clock_col = 0
            return left
        gap = width - left_len - len(clock)
        self._clock_col = left_len + gap + 1
        return left + " " * gap + Theme.CLOCK + clock + Theme.RESET

    @staticmethod
    def _subtitle(geom: Geometry, model: FrameModel) -> str:
        """Machine description on the left, sampling interval on the right.

        The interval sits under the clock rather than in the footer: both are
        "what the numbers on screen refer to", and keeping them together frees
        the footer for keys alone.
        """
        machine = model.machine
        bits = [
            machine.name,
            f"{machine.cores}C/{machine.threads}T",
            "hybrid" if machine.hybrid else "uniform",
            f"up {fmt_duration(model.uptime)}",
        ]
        right = f"Interval {model.interval:.1f}s"
        budget = geom.line_width - len(right) - 2
        sep = f" {Glyph.DOT} "

        # Drop trailing segments rather than cutting a word in half.
        while len(bits) > 1 and 1 + len(sep.join(bits)) > budget:
            bits.pop()
        left = " " + sep.join(bits)
        if len(left) > max(0, budget):
            left = left[: max(0, budget)]

        gap = geom.line_width - len(left) - len(right)
        if gap < 1:
            return Theme.SUBTITLE + left[: geom.line_width] + Theme.RESET
        return (
            Theme.SUBTITLE
            + left
            + Theme.RESET
            + " " * gap
            + Theme.FOOTER_INFO
            + right
            + Theme.RESET
        )

    @staticmethod
    def _column_head(geom: Geometry, plan: CliPlan, model: FrameModel) -> str:
        sep = Theme.RULE + Glyph.V + Theme.RESET + Theme.COLUMN_HEAD
        parts = [Theme.COLUMN_HEAD, " ", "CPU".rjust(W_CPU)]
        if geom.show_type:
            parts += [" ", sep, " ", "TYPE".ljust(W_TYPE)]
        parts += [" ", sep, " ", "USAGE".rjust(W_USAGE)]
        if geom.show_gauge:
            parts += [" ", sep, " ", GAUGE_SCALE_LABEL]
        if geom.show_history:
            # The axis must span exactly the columns that hold data. Stretching
            # it across the empty left margin would claim that the whole row
            # covers the stated time, which it does not until the history has
            # filled the window.
            pad = plan.trend.pad
            window = fmt_window(model.history_span) if model.history_span > 0 else ""
            axis = CliRenderer._axis_bar(max(0, geom.history_width - pad), window)
            axis = (" " * pad + axis).ljust(geom.history_width)
            parts += [" ", sep, " ", Theme.AXIS, axis[: geom.history_width]]
        parts.append(Theme.RESET)
        return "".join(parts)

    @staticmethod
    def _axis_bar(avail: int, window: str) -> str:
        """A double-headed ruler with the span centred inside it.

            <--- History 20s --------------->

        The bar covers exactly the columns that hold data, so its two heads mark
        the oldest and the newest sample. The label is centred within that span,
        which means it settles at the middle of the column once the history has
        filled the window.
        """
        if avail <= 0:
            return ""
        candidates = [f"History {window}", window] if window else ["History"]

        # The ruler heads carry the meaning, the word does not: give up the label
        # before giving up the arrows.
        for text in candidates:
            label = f" {text} "
            if avail >= len(label) + 2:
                rest = avail - len(label) - 2
                left = rest // 2
                return (
                    Glyph.ARROW_LEFT
                    + Glyph.H * left
                    + label
                    + Glyph.H * (rest - left)
                    + Glyph.ARROW
                )
        for text in candidates:
            if avail >= len(text):
                return text.center(avail)
        return " " * avail

    @staticmethod
    def _column_rule(geom: Geometry, junction: str = Glyph.CROSS) -> str:
        """Rule aligned to the vertical bars of the data rows.

        Each column contributes its width plus the two spaces that flank the bar;
        the last column has no trailing space, hence the -1. `junction` selects
        the glyph where the rule meets a column separator: a cross inside the
        table, a tee at its top and bottom edges.
        """
        widths = [W_CPU]
        if geom.show_type:
            widths.append(W_TYPE)
        widths.append(W_USAGE)
        if geom.show_gauge:
            widths.append(W_GAUGE)
        if geom.show_history:
            widths.append(geom.history_width)

        segments = [w + 2 for w in widths]
        segments[-1] -= 1
        return Theme.RULE + junction.join(Glyph.H * n for n in segments) + Theme.RESET

    @classmethod
    def _row(cls, row: RowModel, geom: Geometry, plan: CliPlan) -> str:
        sep = Theme.RULE + Glyph.V + Theme.RESET
        palette = palette_for(row.metric)
        label_colour = Theme.TOTAL_LABEL if row.kind.is_aggregate else Theme.LABEL

        parts = [" ", label_colour, row.label[:W_CPU].rjust(W_CPU), Theme.RESET]
        if geom.show_type:
            parts += [
                " ", sep, " ",
                cls._detail_colour(row), row.detail[:W_TYPE].ljust(W_TYPE), Theme.RESET,
            ]  # fmt: skip
        parts += [
            " ", sep, " ",
            Theme.USAGE, fmt_percent(row.value).rjust(W_USAGE), Theme.RESET,
        ]  # fmt: skip
        if geom.show_gauge:
            parts += [" ", sep, " ", GAUGE.render(row.value, palette)]
        if geom.show_history:
            parts += [
                " ",
                sep,
                " ",
                SPARK.render(
                    plan.trend.cells,
                    row.samples,
                    palette,
                    Theme.MARKER,
                    # One row spells the new interval out; the rest show the bare
                    # seam, so the label is legible instead of repeated.
                    with_label=row.kind is RowKind.TOTAL,
                ),
            ]
        return "".join(parts)

    @staticmethod
    def _detail_colour(row: RowModel) -> str:
        """Core-class tags get their class colour; counts and sizes are muted."""
        if row.kind.shows_core_class:
            return Theme.class_colour(row.detail)
        return Theme.COUNT

    @staticmethod
    def _footer(geom: Geometry) -> str:
        """Keys only: the type legend lives in the help screen, where every
        possible value can be listed without crowding the dashboard.
        """
        left, _used = _hint_bar(
            tuple((num, f"{label} ") for num, label in _FOOTER_HINTS), geom.line_width
        )
        return left

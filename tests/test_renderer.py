"""The CLI renderer, driven the way the app drives it: plan, build, render."""

from __future__ import annotations

import pytest

from cpumon.app_info import APP_NAME, APP_NAME_SHORT
from cpumon.core.history import HistoryStore
from cpumon.core.model import MemoryInfo, Topology
from cpumon.ui.builder import FrameBuilder
from cpumon.ui.help import HelpContent
from cpumon.ui.model import ScreenKind, Viewport
from cpumon.ui.renders.cli import CliRenderer
from cpumon.ui.renders.cli.clock import Clock
from cpumon.ui.renders.cli.layout import (
    CHROME_MINIMAL,
    W_CPU_USAGE,
    LayoutSolver,
    RowMode,
)
from cpumon.ui.state import UiState
from tests.conftest import all_series_keys, filled_history, plain, visible_rows

FROZEN = "12:34:56"


class FrozenClock(Clock):
    """Removes the only time-dependent part of a frame."""

    def text(self, width_budget: int = Clock.UNLIMITED) -> str:
        return FROZEN if len(FROZEN) <= width_budget else ""


def render(
    topo: Topology,
    cols: int,
    rows: int,
    memory: MemoryInfo | None,
    *,
    state: UiState | None = None,
    marks: tuple[tuple[int, str], ...] = (),
    samples: int = 60,
    history: HistoryStore | None = None,
) -> str:
    """One frame, through the same three steps the application performs."""
    store = (
        history if history is not None else filled_history(all_series_keys(topo), samples, marks)
    )
    ui = state if state is not None else UiState(interval=1.5, uptime=98765.0)
    renderer = CliRenderer(
        topo, has_backing=bool(memory and memory.has_backing), clock=FrozenClock()
    )
    plan = renderer.plan(Viewport(cols, rows), ui, memory, store.marker_state())
    model = FrameBuilder(topo, store).build(plan.request, ui)
    return renderer.render(plan, model)


# --- the contract between the three steps ------------------------------------


def test_the_plan_asks_only_for_what_it_will_draw(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    store = filled_history(all_series_keys(hybrid_topology))
    renderer = CliRenderer(hybrid_topology, clock=FrozenClock())
    plan = renderer.plan(Viewport(200, 60), UiState(), memory, store.marker_state())
    request = plan.request

    assert request.screen is ScreenKind.DASHBOARD
    assert request.sample_count == plan.trend.samples
    # One row per thread, plus TOTAL, the two classes and RAM.
    assert len(request.rows) == hybrid_topology.n_cpus + 4
    assert all(spec.series_key for spec in request.rows)


@pytest.mark.parametrize(
    ("state", "screen"),
    [
        (UiState(help_visible=True), ScreenKind.HELP),
        (UiState(), ScreenKind.DASHBOARD),
    ],
)
def test_screens_that_need_no_data_ask_for_none(
    hybrid_topology: Topology, memory: MemoryInfo, state: UiState, screen: ScreenKind
) -> None:
    store = filled_history(all_series_keys(hybrid_topology))
    renderer = CliRenderer(hybrid_topology, clock=FrozenClock())
    plan = renderer.plan(Viewport(120, 40), state, memory, store.marker_state())
    assert plan.request.screen is screen
    assert plan.request.needs_history is (screen is ScreenKind.DASHBOARD)


def test_an_unusable_viewport_asks_for_no_data(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    store = filled_history(all_series_keys(hybrid_topology))
    renderer = CliRenderer(hybrid_topology, clock=FrozenClock())
    plan = renderer.plan(Viewport(5, 2), UiState(), memory, store.marker_state())
    assert plan.request.screen is ScreenKind.UNAVAILABLE
    assert plan.request.rows == ()
    assert plan.request.sample_count == 0


def test_a_foreign_plan_is_refused(hybrid_topology: Topology) -> None:
    class Alien:
        request = None

    renderer = CliRenderer(hybrid_topology)
    with pytest.raises(TypeError):
        renderer.render(Alien(), None)  # type: ignore[arg-type]


# --- invariants --------------------------------------------------------------


@pytest.mark.parametrize(
    ("cols", "rows"),
    [
        (200, 60), (120, 40), (100, 24), (80, 20), (80, 12), (80, 9), (80, 7),
        (52, 11), (49, 9), (38, 20), (35, 20), (22, 12), (15, 8), (10, 5), (1, 1),
    ],
)  # fmt: skip
def test_no_row_is_wider_than_the_window(
    hybrid_topology: Topology, memory: MemoryInfo, cols: int, rows: int
) -> None:
    frame = render(hybrid_topology, cols, rows, memory)
    for index, text in visible_rows(frame).items():
        assert len(text) <= cols, f"row {index} overflows: {text!r}"


@pytest.mark.parametrize(("cols", "rows"), [(200, 60), (80, 20), (80, 8), (20, 10)])
def test_no_row_is_written_outside_the_window(
    hybrid_topology: Topology, memory: MemoryInfo, cols: int, rows: int
) -> None:
    frame = render(hybrid_topology, cols, rows, memory)
    assert max(visible_rows(frame)) < rows


def test_escape_sequences_are_never_cut(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    """A sliced escape sequence would leak raw bytes onto the screen."""
    store = filled_history(all_series_keys(hybrid_topology))
    for cols in range(10, 210, 7):
        for rows in (8, 12, 20, 40):
            frame = render(hybrid_topology, cols, rows, memory, history=store)
            assert "\x1b" not in plain(frame)


# --- dashboard ---------------------------------------------------------------


def test_dashboard_layout_top_to_bottom(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    rows = visible_rows(render(hybrid_topology, 200, 60, memory))
    assert APP_NAME in rows[0]
    assert FROZEN in rows[0]
    assert hybrid_topology.model_name in rows[1]
    assert "14C/20T" in rows[1]
    assert "hybrid" in rows[1]
    assert "up 1d 03:26:05" in rows[1]
    assert "Interval 1.5s" in rows[1]
    assert "CPU" in rows[3] and "TYPE" in rows[3] and "USAGE" in rows[3]
    assert "History" in rows[3]
    assert "TOTAL" in rows[5]
    assert rows[6].split()[0] == "P"
    assert rows[7].split()[0] == "E"
    assert "RAM" in rows[30]  # below every CPU row, behind its own rule
    assert "F1Help" in rows[59]  # the key bar is pinned to the bottom edge
    assert "qQuit" in rows[59]  # q is the quit key, not Esc


def test_every_thread_gets_a_row(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    rows = visible_rows(render(hybrid_topology, 200, 60, memory))
    labels = [text.split()[0] for text in rows.values() if text.strip()]
    for cpu in hybrid_topology.cpus:
        assert str(cpu.lp_id) in labels


def test_ram_row_shows_capacity_and_swap_is_absent(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    text = " ".join(visible_rows(render(hybrid_topology, 200, 60, memory)).values())
    assert "RAM" in text
    assert "32GB" in text
    assert "SWAP" not in text


def test_swap_row_appears_when_the_platform_reports_one(
    hybrid_topology: Topology, memory_with_swap: MemoryInfo
) -> None:
    text = " ".join(visible_rows(render(hybrid_topology, 200, 60, memory_with_swap)).values())
    assert "RAM" in text
    assert "SWAP" in text
    assert "8GB" in text  # the swap capacity


def test_unknown_memory_falls_back_to_a_dash(hybrid_topology: Topology) -> None:
    rows = visible_rows(render(hybrid_topology, 200, 60, None))
    ram = next(text for text in rows.values() if "RAM" in text)
    assert "--" in ram


def test_narrow_window_drops_columns_in_order(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    def head_at(cols: int) -> str:
        return visible_rows(render(hybrid_topology, cols, 40, memory))[3]

    assert "History" in head_at(60)
    assert "History" not in head_at(40)
    assert "TYPE" in head_at(40)
    assert "TYPE" not in head_at(20)
    assert "USAGE" in head_at(20)


def test_short_window_collapses_to_totals(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    geom = LayoutSolver(hybrid_topology).solve(200, 8)
    assert geom.row_mode is RowMode.TOTAL_ONLY
    text = " ".join(visible_rows(render(hybrid_topology, 200, 8, memory)).values())
    assert "TOTAL" in text
    assert "RAM" in text
    assert "USAGE" not in text  # no column head in the minimal layout


def test_title_degrades_before_it_is_truncated(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    wide = visible_rows(render(hybrid_topology, 200, 40, memory))[0]
    medium = visible_rows(render(hybrid_topology, 50, 40, memory))[0]
    narrow = visible_rows(render(hybrid_topology, 40, 40, memory))[0]
    assert "© " in wide  # the copyright is the first thing given up
    assert "Caporale Simone" in medium
    assert "© " not in medium
    assert APP_NAME in narrow
    assert "Caporale Simone" not in narrow


def test_tiny_window_uses_the_short_name(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    row = visible_rows(render(hybrid_topology, 22, 40, memory))[0]
    assert APP_NAME_SHORT in row
    assert APP_NAME not in row


def test_interval_marker_is_drawn_on_the_trend(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    frame = render(hybrid_topology, 200, 60, memory, marks=((40, " 0.5 "),))
    rows = visible_rows(frame)
    total = next(text for text in rows.values() if "TOTAL" in text)
    assert "0.5" in total.rsplit("│", 1)[-1]  # inside the history column


def test_only_the_total_row_spells_the_marker_out(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    """Repeating the label on every row would be unreadable."""
    rows = visible_rows(render(hybrid_topology, 200, 60, memory, marks=((40, " 0.5 "),)))
    per_cpu = [text for text in rows.values() if text.strip().startswith("7 ")]
    assert per_cpu
    assert "0.5" not in per_cpu[0]
    assert "┊" in per_cpu[0]  # the bare seam instead


def test_axis_reports_the_measured_span(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    # 20 samples 0.75 s apart cover 15 s, measured from the stamps rather than
    # inferred from the interval.
    head = visible_rows(render(hybrid_topology, 200, 60, memory, samples=20))[3]
    assert "History 15s" in head


def test_axis_spans_only_the_cells_that_hold_data(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    """With no samples there is nothing to put a ruler over."""
    head = visible_rows(render(hybrid_topology, 200, 60, memory, samples=0))[3]
    assert "History" not in head
    assert head.rstrip().endswith("│")  # the history column is left blank


# --- other screens -----------------------------------------------------------


@pytest.mark.parametrize(("cols", "rows"), [(13, 8), (10, 8), (20, 3), (200, 6)])
def test_unusable_window_says_so(
    hybrid_topology: Topology, memory: MemoryInfo, cols: int, rows: int
) -> None:
    frame = render(hybrid_topology, cols, rows, memory)
    text = " ".join(visible_rows(frame).values())
    assert "too small" in text
    assert f"{cols}x{rows}" in text
    assert f"min {W_CPU_USAGE}x{CHROME_MINIMAL}" in text  # what it would take


def test_the_smallest_window_still_writes_inside_itself(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    rows = visible_rows(render(hybrid_topology, 1, 1, memory))
    assert max(rows) == 0
    assert all(len(text) <= 1 for text in rows.values())


def test_help_screen_shows_the_page_and_its_position(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    state = UiState(help_visible=True, help_scroll=0)
    rows = visible_rows(render(hybrid_topology, 80, 20, memory, state=state))
    assert APP_NAME in rows[0]
    assert "Esc" in rows[19] and "Scroll" in rows[19]
    assert f"of {HelpContent().line_count()}" in rows[19]


def test_help_credit_line_keeps_its_punctuation(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    """The content carries author and year; the renderer supplies the glyphs."""
    state = UiState(help_visible=True)
    rows = visible_rows(render(hybrid_topology, 80, 20, memory, state=state))
    assert "Caporale Simone · © " in rows[1]


def test_help_scroll_moves_the_text(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    top = visible_rows(render(hybrid_topology, 80, 20, memory, state=UiState(help_visible=True)))
    scrolled = visible_rows(
        render(
            hybrid_topology,
            80,
            20,
            memory,
            state=UiState(help_visible=True, help_scroll=6),
        )
    )
    assert top[0] != scrolled[0]
    assert scrolled[0].strip() == top[6].strip()


def test_help_scroll_is_clamped(hybrid_topology: Topology, memory: MemoryInfo) -> None:
    huge = visible_rows(
        render(
            hybrid_topology,
            80,
            20,
            memory,
            state=UiState(help_visible=True, help_scroll=10_000),
        )
    )
    last = visible_rows(
        render(
            hybrid_topology,
            80,
            20,
            memory,
            state=UiState(help_visible=True, help_scroll=HelpContent().max_scroll(20)),
        )
    )
    assert huge == last


def test_short_help_page_offers_no_scroll_hints(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    """A window tall enough for the whole page must not suggest scrolling."""
    tall = HelpContent().line_count() + 5
    rows = visible_rows(render(hybrid_topology, 80, tall, memory, state=UiState(help_visible=True)))
    assert "Scroll" not in rows[tall - 1]
    assert "Esc" in rows[tall - 1]


# --- partial update ----------------------------------------------------------


def _draw(
    topo: Topology, memory: MemoryInfo, state: UiState, cols: int = 200, rows: int = 40
) -> CliRenderer:
    """Render one frame and hand back the renderer that drew it."""
    store = filled_history(all_series_keys(topo))
    renderer = CliRenderer(topo, clock=FrozenClock())
    plan = renderer.plan(Viewport(cols, rows), state, memory, store.marker_state())
    renderer.render(plan, FrameBuilder(topo, store).build(plan.request, state))
    return renderer


def test_partial_update_is_empty_before_anything_is_drawn(
    hybrid_topology: Topology,
) -> None:
    assert CliRenderer(hybrid_topology, clock=FrozenClock()).partial_update() == ""


def test_partial_update_repaints_just_the_clock(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    update = _draw(hybrid_topology, memory, UiState()).partial_update()
    assert plain(update) == FROZEN
    assert update.startswith("\x1b[1;")  # first row, at the clock column


def test_partial_update_stops_after_a_help_frame(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    """The clock is not on screen while the help is, so nothing may repaint."""
    renderer = _draw(hybrid_topology, memory, UiState())
    assert renderer.partial_update() != ""

    store = filled_history(all_series_keys(hybrid_topology))
    helped = UiState(help_visible=True)
    plan = renderer.plan(Viewport(200, 40), helped, memory, store.marker_state())
    renderer.render(plan, FrameBuilder(hybrid_topology, store).build(plan.request, helped))
    assert renderer.partial_update() == ""


def test_clock_is_dropped_when_it_does_not_fit(
    hybrid_topology: Topology, memory: MemoryInfo
) -> None:
    renderer = _draw(hybrid_topology, memory, UiState(), cols=22)
    assert renderer.partial_update() == ""

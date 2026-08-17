"""Key handling, decoupled from both rendering and sampling.

The controller sees the viewport as a size and nothing else: no geometry, no
renderer, no history. These tests therefore need none of those either.
"""

from __future__ import annotations

import pytest

from cpumon.runtime.controller import InputController
from cpumon.settings import INTERVAL_MAX, INTERVAL_MIN, INTERVAL_STEP
from cpumon.ui.help import HelpContent
from cpumon.ui.model import Viewport
from cpumon.ui.state import UiState

HELP = HelpContent()
VIEWPORT = Viewport(120, 30)


def make(state: UiState | None = None) -> tuple[InputController, UiState, list[float]]:
    ui = state if state is not None else UiState()
    changes: list[float] = []
    return InputController(ui, HELP, on_interval_change=changes.append), ui, changes


# --- dashboard ---------------------------------------------------------------


@pytest.mark.parametrize("key", ["q", "Q", "CTRL_C"])
def test_quit_keys_stop_the_app_without_a_repaint(key: str) -> None:
    controller, state, _ = make()
    assert controller.handle(key, VIEWPORT) is False
    assert state.running is False


def test_unknown_keys_are_ignored() -> None:
    controller, state, _ = make()
    for key in ("x", "F5", "UP", "PGDN", "HOME"):
        assert controller.handle(key, VIEWPORT) is False
    assert state.running is True
    assert state.help_visible is False


def test_escape_does_not_quit_the_dashboard() -> None:
    """q is the quit key; Esc only ever dismisses the help page."""
    controller, state, _ = make()
    assert controller.handle("ESC", VIEWPORT) is False
    assert state.running is True


def test_q_closes_the_help_rather_than_quitting() -> None:
    """One level back, as in a pager: the app survives the first q."""
    controller, state, _ = make(UiState(help_visible=True))
    assert controller.handle("q", VIEWPORT) is True
    assert state.help_visible is False
    assert state.running is True
    # The second q, now on the dashboard, does quit.
    assert controller.handle("q", VIEWPORT) is False
    assert state.running is False


def test_f1_opens_the_help_at_the_top() -> None:
    controller, state, _ = make(UiState(help_scroll=12))
    assert controller.handle("F1", VIEWPORT) is True
    assert state.help_visible is True
    assert state.help_scroll == 0


def test_f2_and_f3_move_the_interval() -> None:
    controller, state, changes = make(UiState(interval=1.0))
    assert controller.handle("F2", VIEWPORT) is True
    assert state.interval == pytest.approx(1.0 - INTERVAL_STEP)
    assert controller.handle("F3", VIEWPORT) is True
    assert state.interval == pytest.approx(1.0)
    assert changes == [pytest.approx(0.9), pytest.approx(1.0)]


def test_interval_is_clamped_and_reports_no_change_at_the_bounds() -> None:
    controller, state, changes = make(UiState(interval=INTERVAL_MIN))
    assert controller.handle("F2", VIEWPORT) is False
    assert state.interval == INTERVAL_MIN
    assert changes == []

    state.interval = INTERVAL_MAX
    assert controller.handle("F3", VIEWPORT) is False
    assert state.interval == INTERVAL_MAX


def test_interval_never_drifts() -> None:
    """Repeated presses must not accumulate binary float error."""
    controller, state, _ = make(UiState(interval=1.0))
    for _ in range(20):
        controller.handle("F3", VIEWPORT)
    for _ in range(20):
        controller.handle("F2", VIEWPORT)
    assert state.interval == 1.0


# --- help screen -------------------------------------------------------------


@pytest.mark.parametrize("key", ["ESC", "F1", "q", "Q"])
def test_help_closes_on_its_own_keys(key: str) -> None:
    controller, state, _ = make(UiState(help_visible=True))
    assert controller.handle(key, VIEWPORT) is True
    assert state.help_visible is False
    assert state.running is True


def test_ctrl_c_still_quits_from_the_help() -> None:
    controller, state, _ = make(UiState(help_visible=True))
    assert controller.handle("CTRL_C", VIEWPORT) is False
    assert state.running is False


def test_arrows_scroll_instead_of_closing() -> None:
    """Closing on any key would make scrolling impossible to discover."""
    controller, state, _ = make(UiState(help_visible=True))
    assert controller.handle("DOWN", VIEWPORT) is True
    assert state.help_visible is True
    assert state.help_scroll == 1
    assert controller.handle("UP", VIEWPORT) is True
    assert state.help_scroll == 0


def test_scrolling_up_at_the_top_changes_nothing() -> None:
    controller, state, _ = make(UiState(help_visible=True))
    assert controller.handle("UP", VIEWPORT) is False
    assert state.help_scroll == 0


def test_page_keys_move_by_almost_a_screen() -> None:
    controller, state, _ = make(UiState(help_visible=True))
    page = HELP.viewport(VIEWPORT.rows) - 1
    assert controller.handle("PGDN", VIEWPORT) is True
    # One line of overlap is kept, and the page never runs past the end.
    assert state.help_scroll == min(page, HELP.max_scroll(VIEWPORT.rows))
    assert controller.handle("PGUP", VIEWPORT) is True
    assert state.help_scroll == 0


def test_home_and_end() -> None:
    controller, state, _ = make(UiState(help_visible=True))
    assert controller.handle("END", VIEWPORT) is True
    assert state.help_scroll == HELP.max_scroll(VIEWPORT.rows)
    assert controller.handle("HOME", VIEWPORT) is True
    assert state.help_scroll == 0


def test_scrolling_stops_at_the_last_page() -> None:
    controller, state, _ = make(UiState(help_visible=True))
    for _ in range(HELP.line_count() + 10):
        controller.handle("DOWN", VIEWPORT)
    assert state.help_scroll == HELP.max_scroll(VIEWPORT.rows)
    assert controller.handle("DOWN", VIEWPORT) is False


def test_a_taller_viewport_shows_more_and_scrolls_less() -> None:
    tall = Viewport(120, HELP.line_count() + 10)
    controller, state, _ = make(UiState(help_visible=True))
    assert HELP.max_scroll(tall.rows) == 0
    assert controller.handle("END", tall) is False
    assert state.help_scroll == 0


def test_interval_keys_are_inert_while_the_help_is_open() -> None:
    controller, state, changes = make(UiState(interval=1.0, help_visible=True))
    for key in ("F2", "F3"):
        assert controller.handle(key, VIEWPORT) is False
    assert state.help_visible is True
    assert state.interval == 1.0
    assert changes == []


def test_scrolling_without_a_viewport_is_a_no_op() -> None:
    """Keys may arrive before the first frame has been laid out."""
    controller, state, _ = make(UiState(help_visible=True))
    assert controller.handle("DOWN", None) is False
    assert state.help_scroll == 0

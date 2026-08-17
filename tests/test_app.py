"""The whole loop, driven by a scripted terminal.

These are the only tests that start the sampling and input threads. Each script
ends with a quit key, and a watchdog turns a loop that refuses to end into a
failed assertion rather than a hung test run.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from cpumon.runtime.app import Application
from cpumon.settings import INTERVAL_STEP
from cpumon.ui.renders.cli import ansi
from tests.conftest import ScriptedTerminal, StubBackend, plain

WATCHDOG_SECONDS = 10.0


@dataclass
class Run:
    exit_code: int
    terminal: ScriptedTerminal
    app: Application
    timed_out: bool

    @property
    def output(self) -> str:
        return "".join(self.terminal.written)

    @property
    def text(self) -> str:
        return plain(self.output)

    @property
    def interval(self) -> float:
        return self.app.state.interval


def run_app(
    keys: Sequence[str],
    sizes: Sequence[tuple[int, int]] = ((120, 40),),
    interval: float = 1.0,
) -> Run:
    terminal = ScriptedTerminal(keys, sizes)
    app = Application(StubBackend(terminal=terminal), interval)
    fired: list[bool] = []

    def give_up() -> None:
        fired.append(True)
        app.state.running = False  # the loop should never need this

    watchdog = threading.Timer(WATCHDOG_SECONDS, give_up)
    watchdog.start()
    try:
        code = app.run()
    finally:
        watchdog.cancel()
    return Run(code, terminal, app, bool(fired))


# --- exit paths ---------------------------------------------------------------


@pytest.mark.parametrize("key", ["q", "CTRL_C"])
def test_quit_key_ends_the_run(key: str) -> None:
    run = run_app([key])
    assert run.timed_out is False
    assert run.exit_code == 0
    assert run.app.state.running is False


def test_terminal_is_set_up_and_restored() -> None:
    run = run_app(["q"])
    assert run.terminal.setup_calls == 1
    assert run.terminal.teardown_calls == 1
    assert run.output.startswith(ansi.HIDE_CURSOR)
    assert run.output.endswith(ansi.SHOW_CURSOR)  # and the cursor comes back


def test_the_screen_is_cleaned_up_on_exit() -> None:
    run = run_app(["q"])
    tail = run.output[-20:]
    assert ansi.CLEAR_SCREEN in tail
    assert ansi.HOME in tail


# --- painting -----------------------------------------------------------------


def test_a_dashboard_is_drawn() -> None:
    run = run_app(["q"])
    assert "TOTAL" in run.text
    assert "RAM" in run.text
    assert "Stub CPU" in run.text
    assert "up 01:10:42" in run.text  # the stub reports 4242 s


def test_help_is_shown_and_closed() -> None:
    run = run_app(["F1", "DOWN", "ESC", "q"])
    assert run.timed_out is False
    assert "toggle this help" in run.text
    assert run.app.state.help_visible is False
    # The dashboard is painted again once the help closes.
    assert run.text.rindex("TOTAL") > run.text.index("toggle this help")


def test_interval_keys_reach_the_sampler() -> None:
    run = run_app(["F3", "F3", "q"], interval=1.0)
    assert run.interval == pytest.approx(1.0 + 2 * INTERVAL_STEP)
    assert "Interval 1.2s" in run.text


def test_an_interval_change_is_marked_on_the_trend() -> None:
    run = run_app(["F2", "q"])
    assert run.app.history.marker_state().markers[-1][1] == " 0.9 "


def test_a_resize_is_picked_up() -> None:
    run = run_app(["q"], sizes=((200, 60), (60, 20)))
    assert run.timed_out is False
    assert run.exit_code == 0
    # Both geometries were drawn: the key bar sits on the last row of each, so
    # its two positions prove the layout was solved again after the resize.
    assert ansi.at(59) in run.output
    assert ansi.at(19) in run.output


def test_an_unusable_size_does_not_stop_the_loop() -> None:
    run = run_app(["q"], sizes=((10, 4),))
    assert run.timed_out is False
    assert "too small" in run.text

"""Key handling: the only writer of :class:`~cpumon.ui.state.UiState`.

The controller never draws and never samples. It answers one question — does
this key change what the screen should show? — and the main loop repaints when
the answer is yes. It knows the viewport only as a size, never as a layout, so
it is independent of which renderer is in use.
"""

from __future__ import annotations

from collections.abc import Callable

from cpumon.runtime.keymap import DASHBOARD_KEYS, GLOBAL_KEYS, HELP_KEYS, Action
from cpumon.settings import INTERVAL_STEP, clamp_interval
from cpumon.ui.help import HelpContent
from cpumon.ui.model import Viewport
from cpumon.ui.state import UiState


class InputController:
    """Translates key presses into state changes."""

    def __init__(
        self,
        state: UiState,
        help_content: HelpContent,
        on_interval_change: Callable[[float], None] | None = None,
    ) -> None:
        self._state = state
        self._help = help_content
        self._on_interval_change = on_interval_change

    def handle(self, key: str, viewport: Viewport | None) -> bool:
        """Apply *key*. Returns True when the frame must be redrawn.

        Quitting returns False on purpose: there is no point painting a frame the
        user will never see.
        """
        action = self._resolve(key)
        if action is None:
            return False
        if action is Action.QUIT:
            self._state.running = False
            return False
        if action is Action.OPEN_HELP:
            self._state.help_visible = True
            self._state.help_scroll = 0
            return True
        if action is Action.CLOSE_HELP:
            self._state.help_visible = False
            return True
        if action in (Action.FASTER, Action.SLOWER):
            return self._shift_interval(-1 if action is Action.FASTER else +1)
        return self._scroll_help(action, viewport)

    # -- internals -----------------------------------------------------------

    def _resolve(self, key: str) -> Action | None:
        if key in GLOBAL_KEYS:
            return GLOBAL_KEYS[key]
        table = HELP_KEYS if self._state.help_visible else DASHBOARD_KEYS
        return table.get(key)

    def _shift_interval(self, direction: int) -> bool:
        """direction -1 speeds up (shorter interval), +1 slows down."""
        current = self._state.interval
        new = clamp_interval(current + direction * INTERVAL_STEP)
        if new == current:
            return False  # already at a bound; nothing to redraw
        self._state.interval = new
        if self._on_interval_change is not None:
            self._on_interval_change(new)
        return True

    def _scroll_help(self, action: Action, viewport: Viewport | None) -> bool:
        if viewport is None:
            return False
        rows = viewport.rows
        page = max(1, self._help.viewport(rows) - 1)
        if action is Action.SCROLL_TOP:
            target = 0
        elif action is Action.SCROLL_BOTTOM:
            target = self._help.max_scroll(rows)
        else:
            steps = {
                Action.SCROLL_UP: -1,
                Action.SCROLL_DOWN: 1,
                Action.PAGE_UP: -page,
                Action.PAGE_DOWN: page,
            }
            if action not in steps:  # pragma: no cover - defensive
                return False
            target = self._state.help_scroll + steps[action]

        target = self._help.clamp_scroll(target, rows)
        if target == self._state.help_scroll:
            return False
        self._state.help_scroll = target
        return True

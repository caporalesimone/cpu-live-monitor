"""What each key means, per screen.

Key names are the normalised ones produced by the terminal backends, so the
bindings are the same on every platform.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Final


class Action(Enum):
    """An intent, independent of the key that produced it."""

    QUIT = auto()
    OPEN_HELP = auto()
    CLOSE_HELP = auto()
    FASTER = auto()
    SLOWER = auto()
    SCROLL_UP = auto()
    SCROLL_DOWN = auto()
    PAGE_UP = auto()
    PAGE_DOWN = auto()
    SCROLL_TOP = auto()
    SCROLL_BOTTOM = auto()


# Recognised on every screen: Ctrl-C always means stop.
GLOBAL_KEYS: Final[dict[str, Action]] = {"CTRL_C": Action.QUIT}

DASHBOARD_KEYS: Final[dict[str, Action]] = {
    "q": Action.QUIT,
    "Q": Action.QUIT,
    "F1": Action.OPEN_HELP,
    "F2": Action.FASTER,
    "F3": Action.SLOWER,
}

# The help page deliberately answers to navigation and closing only. Closing on
# any key would make scrolling impossible to discover: the first arrow press
# would dismiss the page the user is trying to read.
#
# `q` closes the page here rather than quitting the app: it goes back one level,
# the way it does in a pager. Esc does the same, and is kept for the page even
# though the dashboard no longer answers to it.
HELP_KEYS: Final[dict[str, Action]] = {
    "ESC": Action.CLOSE_HELP,
    "F1": Action.CLOSE_HELP,
    "q": Action.CLOSE_HELP,
    "Q": Action.CLOSE_HELP,
    "UP": Action.SCROLL_UP,
    "DOWN": Action.SCROLL_DOWN,
    "PGUP": Action.PAGE_UP,
    "PGDN": Action.PAGE_DOWN,
    "HOME": Action.SCROLL_TOP,
    "END": Action.SCROLL_BOTTOM,
}

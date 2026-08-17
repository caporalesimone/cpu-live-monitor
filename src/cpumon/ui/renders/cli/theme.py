"""Every colour in the program, in one place.

Changing the look of the tool means editing this module only. Colour lookup
per load value lives in :mod:`cpumon.ui.palette`, which caches one entry per
integer percent so the render loop never branches.
"""

from __future__ import annotations

from typing import ClassVar


def _fg(n: int) -> str:
    """256-colour foreground escape."""
    return f"\x1b[38;5;{n}m"


def _bg(n: int) -> str:
    """256-colour background escape."""
    return f"\x1b[48;5;{n}m"


class Theme:
    """Named styles, grouped by what they dress."""

    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"

    # --- chrome -------------------------------------------------------------
    TITLE = BOLD + _fg(255)
    VERSION = _fg(244)
    SUBTITLE = _fg(245)
    CLOCK = _fg(252)
    RULE = _fg(238)  # all table separators, horizontal and vertical
    COLUMN_HEAD = _fg(246)
    AXIS = _fg(240)
    MARKER = _fg(250)  # interval-change label drawn over the trend

    # --- values -------------------------------------------------------------
    LABEL = _fg(252)
    TOTAL_LABEL = BOLD + _fg(255)
    USAGE = _fg(252)
    COUNT = _fg(243)

    # --- core classes -------------------------------------------------------
    # Keys are the type tags produced by the model layer.
    CLASS: ClassVar[dict[str, str]] = {
        "P": _fg(75),
        "PHT": _fg(67),
        "E": _fg(114),
        "EHT": _fg(107),
        "LPE": _fg(80),
        "LPEH": _fg(66),
        "?": _fg(244),
    }
    CLASS_DEFAULT = _fg(244)

    # --- load gradients -----------------------------------------------------
    # Each entry is (inclusive upper bound, colour); the bands are therefore
    # 0..a, a+1..b, b+1..100, and the last entry catches everything above. CPU
    # and memory have different tolerances, so they get independent scales.
    OK = _fg(78)  # calm green
    WARN_ = _fg(179)  # amber
    HOT = _fg(203)  # red

    CPU_LOAD_STEPS: ClassVar[tuple[tuple[int, str], ...]] = (
        (39, OK),  # 0-39   green
        (74, WARN_),  # 40-74  amber
        (100, HOT),  # 75-100 red
    )
    MEM_LOAD_STEPS: ClassVar[tuple[tuple[int, str], ...]] = (
        (49, OK),  # 0-49   green
        (74, WARN_),  # 50-74  amber
        (100, HOT),  # 75-100 red
    )

    # --- footer / help ------------------------------------------------------
    KEY_NUM = _fg(252)
    KEY_LABEL = _fg(235) + _bg(109)
    FOOTER_INFO = _fg(244)
    HELP_TITLE = BOLD + _fg(255)
    HELP_BODY = _fg(250)
    WARNING = _fg(203)

    @classmethod
    def class_colour(cls, tag: str) -> str:
        return cls.CLASS.get(tag, cls.CLASS_DEFAULT)

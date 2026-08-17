"""The wall clock, formatted as wide as the space allows."""

from __future__ import annotations

import time
from typing import ClassVar


class Clock:
    """Formats the current time as wide as the space allows."""

    FORMATS: ClassVar[tuple[str, ...]] = (
        "%a %d %b %Y  %H:%M:%S",
        "%d %b  %H:%M:%S",
        "%H:%M:%S",
    )
    UNLIMITED: ClassVar[int] = 999

    def text(self, width_budget: int = UNLIMITED) -> str:
        """Longest clock format that fits *width_budget*, or "" if none does."""
        for fmt in self.FORMATS:
            text = time.strftime(fmt)
            if len(text) <= width_budget:
                return text
        return ""

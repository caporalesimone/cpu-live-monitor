"""Load-to-colour mapping, precomputed once per metric."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from cpumon.ui.model import MetricKind
from cpumon.ui.renders.cli.theme import Theme


class LoadPalette:
    """Colour lookup for one metric, cached per integer percent.

    Bands come straight from a Theme step list, so adding or moving a
    threshold is a one-line change in Theme and nothing else.
    """

    __slots__ = ("colour", "steps")

    def __init__(self, steps: Sequence[tuple[int, str]]) -> None:
        self.steps = tuple(steps)
        self.colour: list[str] = [self._pick(p) for p in range(101)]

    def _pick(self, percent: int) -> str:
        for upper, colour in self.steps:
            if percent <= upper:
                return colour
        return self.steps[-1][1]

    def bands(self) -> list[tuple[int, int, str]]:
        """(low, high, colour) for each band, inclusive on both ends."""
        out: list[tuple[int, int, str]] = []
        low = 0
        for upper, colour in self.steps:
            out.append((low, upper, colour))
            low = upper + 1
        return out


PALETTE_CPU: Final = LoadPalette(Theme.CPU_LOAD_STEPS)
PALETTE_MEM: Final = LoadPalette(Theme.MEM_LOAD_STEPS)

# The only place a metric turns into colours. Rows carry a MetricKind and know
# nothing about which scale it maps to.
_BY_METRIC: Final[dict[MetricKind, LoadPalette]] = {
    MetricKind.CPU: PALETTE_CPU,
    MetricKind.MEMORY: PALETTE_MEM,
}


def palette_for(metric: MetricKind) -> LoadPalette:
    return _BY_METRIC[metric]

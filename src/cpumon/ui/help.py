"""The help page as content, not as text on a screen.

Each line says what kind of line it is; a renderer decides how it looks. The
legend lines carry only what they are a legend *of* — a core-class tag, a metric
— so the renderer can illustrate them with its own colours, bars and glyphs, and
a renderer with no colours can spell the same thing out in words.

Scrolling arithmetic lives here too: the controller must be able to page the
document without knowing which renderer is drawing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar, Final

from cpumon.app_info import APP_AUTHOR, APP_NAME, APP_VERSION, APP_YEAR
from cpumon.settings import INTERVAL_MAX, INTERVAL_MIN, INTERVAL_STEP
from cpumon.ui.model import MetricKind

# Rows held back from the text: the last for the key bar, and the one above it
# left blank so the text never touches it. On a viewport too short for both, the
# blank row is the first thing given up.
_RESERVED_ROWS: Final = 2


class HelpLineKind(Enum):
    """What a help line is, which is all a renderer needs to style it."""

    TITLE = auto()
    BODY = auto()
    SUBTLE = auto()
    BLANK = auto()
    CREDIT = auto()  # author and year; the renderer supplies the punctuation
    CORE_CLASS = auto()  # one core-class tag and what it means
    COLOUR_BANDS = auto()  # the load bands of one metric
    GAUGE_SCALE = auto()  # the gauge at a few values of one metric
    TREND_RAMP = auto()  # a low-to-high sample ramp


@dataclass(frozen=True)
class HelpLine:
    """One line of the document."""

    kind: HelpLineKind
    text: str = ""
    label: str = ""  # leading label of a legend line
    tag: str = ""  # core-class tag being explained
    metric: MetricKind | None = None


def _title(text: str) -> HelpLine:
    return HelpLine(HelpLineKind.TITLE, text)


def _body(text: str) -> HelpLine:
    return HelpLine(HelpLineKind.BODY, text)


_BLANK: Final = HelpLine(HelpLineKind.BLANK)


class HelpContent:
    """The help document, built once, plus the arithmetic for scrolling it."""

    # Every tag LogicalCpu.type_tag can produce, with what it means. Listed in
    # full rather than filtered to the current machine, so the help doubles as
    # documentation of the classification itself.
    CORE_CLASSES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("P", "performance core, primary thread"),
        ("PHT", "performance core, SMT sibling"),
        ("E", "efficiency core"),
        ("EHT", "efficiency core, SMT sibling"),
        ("LPE", "low-power efficiency core, SoC tile"),
        ("LPEH", "low-power efficiency core, SMT sibling"),
        ("?", "class not reported by the operating system"),
    )

    def __init__(self) -> None:
        self._lines: tuple[HelpLine, ...] = tuple(self._build())

    # -- content -------------------------------------------------------------

    def lines(self) -> tuple[HelpLine, ...]:
        return self._lines

    def line_count(self) -> int:
        return len(self._lines)

    @classmethod
    def _build(cls) -> list[HelpLine]:
        return [
            _title(f"{APP_NAME} v{APP_VERSION}"),
            HelpLine(HelpLineKind.CREDIT, APP_YEAR, label=APP_AUTHOR),
            _BLANK,
            _title("keys"),
            _body("F1    toggle this help"),
            _body("      Up/Down and PgUp/PgDn scroll it, Esc closes"),
            _body(f"F2    faster sampling  (-{INTERVAL_STEP:.1f}s)"),
            _body(f"F3    slower sampling  (+{INTERVAL_STEP:.1f}s)"),
            _body(f"      range {INTERVAL_MIN:.1f}s to {INTERVAL_MAX:.1f}s"),
            _body("q     quit  (Ctrl-C too)"),
            _BLANK,
            _title("columns"),
            _body("CPU     logical processor id; an id pair or range when folded"),
            _body("TYPE    core class, or memory capacity on the RAM row"),
            _body("USAGE   busy share since the previous sample"),
            _body("gauge   the same value, one cell per 10%"),
            _body("History oldest sample left, newest right; the ruler above"),
            _body("        spans the samples on screen and states their age"),
            _body("        a gap with a figure marks an interval change;"),
            _body("        cells left of it cover a different amount of time"),
            _body("RAM     used share of physical memory; TYPE shows capacity"),
            _body("SWAP    pages written out to swap devices. Linux only, and"),
            _body("        hidden when no swap is configured. Windows shows no"),
            _body("        such row: its commit charge tracks RAM too closely"),
            _body("        to be worth a line, and pagefile use says little"),
            _body("        about pressure because the system writes there"),
            _body("        proactively even with memory to spare."),
            _BLANK,
            _title("core types"),
            *(
                HelpLine(HelpLineKind.CORE_CLASS, meaning, tag=tag)
                for tag, meaning in cls.CORE_CLASSES
            ),
            _BLANK,
            _title("colour thresholds"),
            HelpLine(HelpLineKind.COLOUR_BANDS, label="CPU", metric=MetricKind.CPU),
            HelpLine(HelpLineKind.COLOUR_BANDS, label="RAM", metric=MetricKind.MEMORY),
            _BLANK,
            _title("gauge at a glance"),
            HelpLine(HelpLineKind.GAUGE_SCALE, label="CPU", metric=MetricKind.CPU),
            HelpLine(HelpLineKind.GAUGE_SCALE, label="RAM", metric=MetricKind.MEMORY),
            HelpLine(
                HelpLineKind.SUBTLE,
                "      note 45% is amber for CPU but still green for RAM",
            ),
            HelpLine(
                HelpLineKind.TREND_RAMP,
                "   idle on the left, busiest on the right",
                label="trend",
            ),
            _BLANK,
            _title("layout"),
            _body("Shrinking the window folds SMT siblings, then groups cores,"),
            _body("then collapses to per-class rows, then to totals alone."),
            _body("Narrowing drops history, then the gauge, then the type column."),
            _BLANK,
        ]

    # -- scrolling -----------------------------------------------------------

    @staticmethod
    def viewport(rows: int) -> int:
        """Lines of the document visible at once in a viewport *rows* tall."""
        return max(1, rows - _RESERVED_ROWS)

    def max_scroll(self, rows: int) -> int:
        return max(0, self.line_count() - self.viewport(rows))

    def clamp_scroll(self, scroll: int, rows: int) -> int:
        return max(0, min(scroll, self.max_scroll(rows)))

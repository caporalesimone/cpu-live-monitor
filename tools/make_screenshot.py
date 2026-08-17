"""Render a real frame and save it as an SVG "screenshot".

    python tools/make_screenshot.py --seconds 40 -o docs/screenshot.svg

The frame comes from the actual renderer, fed by the actual sampler watching this
machine, so every glyph, colour and column is what a terminal would show. Only
the medium changes: the ANSI escapes are translated into SVG text runs, using the
xterm 256-colour palette the theme is written against.

Regenerate it whenever the look changes — the image is documentation, and stale
documentation is worse than none.
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path

from cpumon.backend import create_backend
from cpumon.runtime.session import MonitorSession
from cpumon.ui.model import Viewport
from cpumon.ui.renders.cli import CliRenderer

# Cell metrics, in SVG user units. The advance is pinned so box drawing lines up
# regardless of which monospace font the viewer resolves.
CELL_W = 8.0
CELL_H = 17.0
FONT_SIZE = 14.0
BASELINE = 13.0
PADDING = 12.0
BACKGROUND = "#101014"
DEFAULT_FG = "#c8c8c8"
FONT_STACK = "'Cascadia Mono','JetBrains Mono','DejaVu Sans Mono','Consolas','Menlo',monospace"

_ROW_START = re.compile(r"\x1b\[(\d+);1H\x1b\[2K")
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_XTERM_CUBE = (0, 95, 135, 175, 215, 255)

# Block elements are drawn as rectangles rather than as glyphs. A gauge or a
# sparkline is a geometric shape that happens to be encoded as a character: left
# as text, its width and its coverage depend on the font, and neighbouring cells
# show hairline seams where the colour changes. As rects, on integer coordinates
# and with crisp edges, adjacent cells meet exactly.
_FULL_BLOCK = "█"
_LOWER_BLOCKS = "▁▂▃▄▅▆▇"  # U+2581..U+2587: the bottom 1/8 .. 7/8 of the cell
_LEFT_BLOCKS = "▏▎▍▌▋▊▉"  # U+258F..U+2589: the left 1/8 .. 7/8 of the cell
_EIGHTHS = 8


def block_geometry(char: str) -> tuple[float, float, float] | None:
    """(y offset, width, height) of a block glyph, in fractions of a cell."""
    if char == _FULL_BLOCK:
        return 0.0, 1.0, 1.0
    if char in _LOWER_BLOCKS:
        filled = (_LOWER_BLOCKS.index(char) + 1) / _EIGHTHS
        return 1.0 - filled, 1.0, filled
    if char in _LEFT_BLOCKS:
        return 0.0, (_LEFT_BLOCKS.index(char) + 1) / _EIGHTHS, 1.0
    return None


def xterm_colour(index: int) -> str:
    """The xterm 256-colour palette entry, as #rrggbb."""
    if index < 16:  # not used by the theme, but complete for safety
        base = (0, 128)[index >= 8]
        return f"#{base:02x}{base:02x}{base:02x}"
    if index < 232:
        offset = index - 16
        r, g, b = offset // 36, (offset % 36) // 6, offset % 6
        return f"#{_XTERM_CUBE[r]:02x}{_XTERM_CUBE[g]:02x}{_XTERM_CUBE[b]:02x}"
    grey = 8 + 10 * (index - 232)
    return f"#{grey:02x}{grey:02x}{grey:02x}"


@dataclass(frozen=True)
class Style:
    fg: str | None = None
    bg: str | None = None
    bold: bool = False


@dataclass(frozen=True)
class Run:
    row: int
    col: int
    text: str
    style: Style


def apply_sgr(style: Style, params: str) -> Style:
    """Fold one SGR sequence into the running style."""
    codes = [int(p) for p in params.split(";") if p != ""] or [0]
    index = 0
    while index < len(codes):
        code = codes[index]
        if code == 0:
            style = Style()
        elif code == 1:
            style = replace(style, bold=True)
        elif code == 38 and codes[index + 1 : index + 2] == [5]:
            style = replace(style, fg=xterm_colour(codes[index + 2]))
            index += 2
        elif code == 48 and codes[index + 1 : index + 2] == [5]:
            style = replace(style, bg=xterm_colour(codes[index + 2]))
            index += 2
        index += 1
    return style


def parse_frame(frame: str) -> tuple[list[Run], int, int]:
    """Split a rendered frame into styled runs, plus the grid it occupies."""
    runs: list[Run] = []
    style = Style()
    max_col = 0
    max_row = 0

    parts = _ROW_START.split(frame)
    # parts alternates: [prefix, row, content, row, content, ...]
    for i in range(1, len(parts) - 1, 2):
        row = int(parts[i]) - 1
        max_row = max(max_row, row)
        col = 0
        for piece_index, piece in enumerate(_SGR.split(parts[i + 1])):
            if piece_index % 2:  # the captured SGR parameters
                style = apply_sgr(style, piece)
                continue
            if not piece:
                continue
            if piece.strip() or style.bg is not None:
                runs.append(Run(row, col, piece, style))
            col += len(piece)
        max_col = max(max_col, col)
    return runs, max_col, max_row + 1


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_svg(runs: list[Run], cols: int, rows: int, title: str) -> str:
    width = cols * CELL_W + 2 * PADDING
    height = rows * CELL_H + 2 * PADDING
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="{FONT_STACK}" font-size="{FONT_SIZE}">',
        f"<title>{escape(title)}</title>",
        f'<rect width="100%" height="100%" rx="6" fill="{BACKGROUND}"/>',
    ]

    # Backgrounds first, so no glyph is painted over.
    for run in runs:
        if run.style.bg is None:
            continue
        x = PADDING + run.col * CELL_W
        y = PADDING + run.row * CELL_H
        out.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{len(run.text) * CELL_W:.1f}" '
            f'height="{CELL_H:.1f}" fill="{run.style.bg}"/>'
        )

    for run in runs:
        if not run.text.strip():
            continue
        out.extend(draw_run(run))

    out.append("</svg>")
    return "\n".join(out)


def draw_run(run: Run) -> list[str]:
    """One run as SVG: block glyphs become rects, everything else stays text."""
    out: list[str] = []
    colour = run.style.fg or DEFAULT_FG
    weight = ' font-weight="bold"' if run.style.bold else ""
    pending: list[str] = []  # characters waiting to be emitted as text
    pending_col = run.col

    def flush_text(col: int) -> None:
        if not pending:
            return
        text = "".join(pending)
        x = PADDING + col * CELL_W
        y = PADDING + run.row * CELL_H + BASELINE
        out.append(
            f'<text x="{x:.1f}" y="{y:.1f}" fill="{colour}"{weight} '
            f'textLength="{len(text) * CELL_W:.1f}" lengthAdjust="spacingAndGlyphs" '
            f'xml:space="preserve">{escape(text)}</text>'
        )
        pending.clear()

    index = 0
    while index < len(run.text):
        char = run.text[index]
        shape = block_geometry(char)
        if shape is None:
            if not pending:
                pending_col = run.col + index
            pending.append(char)
            index += 1
            continue

        flush_text(pending_col)
        y_offset, width, height = shape
        # Identical full-width neighbours become one rectangle, so nothing meets
        # in between. A part-width glyph cannot be merged: it leaves a real gap.
        span = 1
        if width == 1.0:
            while index + span < len(run.text) and run.text[index + span] == char:
                span += 1
        out.append(
            f'<rect x="{PADDING + (run.col + index) * CELL_W:.1f}" '
            f'y="{PADDING + run.row * CELL_H + y_offset * CELL_H:.2f}" '
            f'width="{(span - 1 + width) * CELL_W:.1f}" '
            f'height="{height * CELL_H:.2f}" fill="{colour}" '
            f'shape-rendering="crispEdges"/>'
        )
        index += span

    flush_text(pending_col)
    return out


def capture(cols: int, rows: int, seconds: float, interval: float) -> str:
    """Watch this machine for a while, then render one frame of what it saw."""
    session = MonitorSession.create(create_backend(), interval)
    worker = session.worker(lambda: None)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(interval)
        worker.collect_once()
    session.refresh_uptime()

    renderer = CliRenderer(session.topology, has_backing=session.has_backing)
    plan = renderer.plan(
        Viewport(cols, rows), session.state, session.memory_info, session.markers()
    )
    return renderer.render(plan, session.build(plan.request))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="make_screenshot", description="render a real frame as an SVG"
    )
    parser.add_argument("-o", "--output", type=Path, default=Path("docs/screenshot.svg"))
    parser.add_argument("--cols", type=int, default=104)
    parser.add_argument("--rows", type=int, default=27)
    parser.add_argument("-s", "--seconds", type=float, default=40.0)
    parser.add_argument("-i", "--interval", type=float, default=0.5)
    args = parser.parse_args()

    frame = capture(args.cols, args.rows, args.seconds, args.interval)
    runs, cols, rows = parse_frame(frame)
    svg = to_svg(runs, max(cols, args.cols), rows, "CPU LIVE MONITOR")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")

    print(f"wrote   : {args.output}")
    print(f"grid    : {cols}x{rows} cells, {len(runs)} styled runs")
    print(f"size    : {args.output.stat().st_size / 1024:.1f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

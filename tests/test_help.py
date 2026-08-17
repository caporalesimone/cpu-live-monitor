"""Help content and its CLI styling, checked on both sides of the boundary."""

from __future__ import annotations

import pytest

from cpumon.app_info import APP_AUTHOR, APP_NAME, APP_VERSION, APP_YEAR
from cpumon.settings import INTERVAL_MAX, INTERVAL_MIN, INTERVAL_STEP
from cpumon.ui.help import HelpContent, HelpLine, HelpLineKind
from cpumon.ui.model import MetricKind
from cpumon.ui.renders.cli.helpstyle import style_help
from tests.conftest import plain

HELP = HelpContent()


# --- content (no renderer involved) -------------------------------------------


def test_the_content_is_free_of_escape_sequences() -> None:
    """A second renderer must not inherit ANSI baked into the text."""
    for line in HELP.lines():
        for field in (line.text, line.label, line.tag):
            assert "\x1b" not in field


def test_it_documents_itself() -> None:
    text = " ".join(line.text for line in HELP.lines())
    assert f"{APP_NAME} v{APP_VERSION}" in text
    assert "toggle this help" in text
    assert f"-{INTERVAL_STEP:.1f}s" in text
    assert f"{INTERVAL_MIN:.1f}s to {INTERVAL_MAX:.1f}s" in text


def test_the_credit_line_carries_data_not_punctuation() -> None:
    credit = next(line for line in HELP.lines() if line.kind is HelpLineKind.CREDIT)
    assert credit.label == APP_AUTHOR
    assert credit.text == APP_YEAR


def test_every_core_class_is_documented() -> None:
    tags = [line.tag for line in HELP.lines() if line.kind is HelpLineKind.CORE_CLASS]
    assert tags == ["P", "PHT", "E", "EHT", "LPE", "LPEH", "?"]


def test_both_metrics_get_a_legend() -> None:
    for kind in (HelpLineKind.COLOUR_BANDS, HelpLineKind.GAUGE_SCALE):
        metrics = [line.metric for line in HELP.lines() if line.kind is kind]
        assert metrics == [MetricKind.CPU, MetricKind.MEMORY]


# --- scrolling ---------------------------------------------------------------


def test_two_rows_are_reserved_for_the_key_bar() -> None:
    assert HELP.viewport(40) == 38
    assert HELP.viewport(3) == 1
    assert HELP.viewport(1) == 1  # never zero


def test_max_scroll_reaches_the_last_line_and_no_further() -> None:
    rows = 20
    assert HELP.max_scroll(rows) == HELP.line_count() - HELP.viewport(rows)
    assert HELP.clamp_scroll(10_000, rows) == HELP.max_scroll(rows)
    assert HELP.clamp_scroll(-5, rows) == 0


def test_a_tall_viewport_needs_no_scrolling() -> None:
    assert HELP.max_scroll(HELP.line_count() + 5) == 0


# --- styling (the CLI side) ---------------------------------------------------


def test_every_line_styles_to_exactly_one_line() -> None:
    """The scroll arithmetic depends on a 1:1 mapping."""
    for line in HELP.lines():
        for variant in style_help(line):
            assert "\n" not in variant


def test_the_plain_variant_measures_the_styled_one() -> None:
    for line in HELP.lines():
        text, styled = style_help(line)
        assert plain(styled) == text


def test_blank_lines_stay_blank() -> None:
    assert style_help(HelpLine(HelpLineKind.BLANK)) == ("", "")


def test_a_core_class_line_is_coloured_by_its_tag() -> None:
    text, styled = style_help(HelpLine(HelpLineKind.CORE_CLASS, "efficiency core", tag="E"))
    assert text == "E     efficiency core"
    assert styled.startswith("\x1b[38;5;114m")  # the E colour, from the theme


def test_band_and_gauge_legends_differ_between_metrics() -> None:
    cpu = style_help(HelpLine(HelpLineKind.COLOUR_BANDS, label="CPU", metric=MetricKind.CPU))
    mem = style_help(HelpLine(HelpLineKind.COLOUR_BANDS, label="RAM", metric=MetricKind.MEMORY))
    assert cpu[0] != mem[0]  # the band ranges are not the same
    assert "0-39%" in cpu[0]
    assert "0-49%" in mem[0]


def test_the_trend_ramp_climbs() -> None:
    text, _ = style_help(HelpLine(HelpLineKind.TREND_RAMP, " caption", label="trend"))
    ramp = text.removeprefix("trend ").removesuffix(" caption")
    assert len(ramp) == 16
    assert ramp[0] != ramp[-1]


@pytest.mark.parametrize("kind", [HelpLineKind.TITLE, HelpLineKind.BODY, HelpLineKind.SUBTLE])
def test_flat_kinds_keep_their_text(kind: HelpLineKind) -> None:
    text, styled = style_help(HelpLine(kind, "hello"))
    assert text == "hello"
    assert plain(styled) == "hello"

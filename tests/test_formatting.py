"""The formatters carry the column alignment, so their widths are the contract."""

from __future__ import annotations

import pytest

from cpumon.ui.renders.cli.formatting import (
    capacity_label,
    clamp_percent,
    fmt_duration,
    fmt_percent,
    fmt_window,
)
from cpumon.ui.renders.cli.layout import W_TYPE, W_USAGE


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-5.0, "0.0%"),
        (0.0, "0.0%"),
        (0.04, "0.0%"),
        (12.34, "12.3%"),
        (99.94, "99.9%"),
        (99.95, "100%"),
        (99.96, "100%"),  # would be "100.0%" (6 chars) if rounded naively
        (100.0, "100%"),
        (250.0, "100%"),
    ],
)
def test_fmt_percent_values(value: float, expected: str) -> None:
    assert fmt_percent(value) == expected


def test_fmt_percent_never_exceeds_the_usage_column() -> None:
    for i in range(0, 100_001):
        assert len(fmt_percent(i / 1000.0)) <= W_USAGE


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-1.0, 0), (0.0, 0), (0.5, 1), (49.4, 49), (49.5, 50), (100.0, 100), (150.0, 100)],
)
def test_clamp_percent(value: float, expected: int) -> None:
    assert clamp_percent(value) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00:00"),
        (59, "00:00:59"),
        (3600, "01:00:00"),
        (86399, "23:59:59"),
        (86400, "1d 00:00:00"),
        (90061, "1d 01:01:01"),
    ],
)
def test_fmt_duration(seconds: int, expected: str) -> None:
    assert fmt_duration(seconds) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "0.0s"),
        (9.94, "9.9s"),
        (9.95, "10s"),
        (59.4, "59s"),
        (59.6, "1m 00s"),  # rounds before choosing the unit
        (60.0, "1m 00s"),
        (125.0, "2m 05s"),
        (3599.0, "59m 59s"),
        (3600.0, "1h 00m"),
        (7380.0, "2h 03m"),
    ],
)
def test_fmt_window(seconds: float, expected: str) -> None:
    assert fmt_window(seconds) == expected


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0, "0GB"),
        (8 * 2**30, "8GB"),
        (32 * 2**30, "32GB"),
        (128 * 2**30, "128G"),  # "128GB" would overflow the TYPE column
        (1024 * 2**30, "1024"),
    ],
)
def test_capacity_label(total: int, expected: str) -> None:
    assert capacity_label(total) == expected


def test_capacity_label_fits_the_type_column() -> None:
    for gib in range(0, 4097):
        assert len(capacity_label(gib * 2**30)) <= W_TYPE

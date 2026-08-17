"""Number and duration formatting, kept apart from the widgets that use it.

Every function here is a pure string transform, so the exact wording and the
exact widths are testable without a terminal.
"""

from __future__ import annotations

from cpumon.ui.renders.cli.layout import W_TYPE

_BYTES_PER_GIB = 1024**3
_SECONDS_PER_DAY = 86400
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_MINUTE = 60


def fmt_percent(value: float) -> str:
    """Percentage that never exceeds five characters.

    One decimal below full scale, none at full scale. The threshold is 99.95
    and not 100.0 because ``f"{99.96:.1f}%"`` is "100.0%", which would be six
    characters and push the whole row one column out of alignment.
    """
    if value >= 99.95:
        return "100%"
    if value <= 0.0:
        return "0.0%"
    return f"{value:.1f}%"


def clamp_percent(value: float) -> int:
    """Round to the nearest whole percent, held inside 0..100."""
    if value <= 0.0:
        return 0
    if value >= 100.0:
        return 100
    return int(value + 0.5)


def fmt_duration(seconds: float) -> str:
    """Uptime style: 'HH:MM:SS', prefixed with days once there are any."""
    total = int(seconds)
    days, rem = divmod(total, _SECONDS_PER_DAY)
    hours, rem = divmod(rem, _SECONDS_PER_HOUR)
    minutes, secs = divmod(rem, _SECONDS_PER_MINUTE)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def fmt_window(seconds: float) -> str:
    """Compact duration: seconds up to 59, then minutes and seconds.

    Rounding is applied before choosing the unit, otherwise 59.6 s would be
    formatted as "60s" instead of rolling over to "1m 00s".
    """
    if seconds < 9.95:
        return f"{seconds:.1f}s"
    total = round(seconds)
    if total < _SECONDS_PER_MINUTE:
        return f"{total}s"
    if total < _SECONDS_PER_HOUR:
        minutes, secs = divmod(total, _SECONDS_PER_MINUTE)
        return f"{minutes}m {secs:02d}s"
    hours, rest = divmod(total, _SECONDS_PER_HOUR)
    return f"{hours}h {rest // _SECONDS_PER_MINUTE:02d}m"


def capacity_label(total_bytes: int) -> str:
    """Size for the TYPE column: "32GB", falling back to "128G"."""
    gib = total_bytes / _BYTES_PER_GIB
    text = f"{gib:.0f}GB"
    return text if len(text) <= W_TYPE else f"{gib:.0f}G"[:W_TYPE]

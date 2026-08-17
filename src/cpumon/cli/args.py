"""Argument parsing for the cpumon CLI (argparse only)."""

from __future__ import annotations

import argparse

from cpumon.app_info import APP_NAME, APP_VERSION
from cpumon.cli import COMMAND_NAME
from cpumon.settings import INTERVAL_MAX, INTERVAL_MIN

DEFAULT_INTERVAL = 1.0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(prog=COMMAND_NAME, description=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"{COMMAND_NAME} {APP_VERSION}",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=(
            f"initial sampling interval in seconds ({INTERVAL_MIN:.1f}-{INTERVAL_MAX:.1f}, clamped)"
        ),
    )
    parser.add_argument(
        "--selftest",
        nargs=2,
        metavar=("COLS", "ROWS"),
        type=int,
        help="render one frame at the given size and exit",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="print backend diagnostics and exit",
    )
    return parser

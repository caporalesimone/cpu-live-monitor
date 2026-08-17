"""CLI surface: parsing, and the two non-interactive commands.

The interactive path is deliberately not started here: it takes over the
terminal and only an Esc or a Ctrl-C would end it.
"""

from __future__ import annotations

import pytest

from cpumon.app_info import APP_NAME, APP_VERSION
from cpumon.backend import create_backend
from cpumon.cli import COMMAND_NAME
from cpumon.cli.args import DEFAULT_INTERVAL, build_parser
from cpumon.cli.exit_codes import ExitCode
from cpumon.cli.main import main


def platform_supported() -> bool:
    try:
        create_backend()
    except Exception:
        return False
    return True


requires_backend = pytest.mark.skipif(
    not platform_supported(), reason="no backend for this platform"
)


# --- parsing -----------------------------------------------------------------


def test_defaults():
    args = build_parser().parse_args([])
    assert args.interval == DEFAULT_INTERVAL
    assert args.selftest is None
    assert args.probe is False


def test_interval_is_parsed_as_a_float():
    assert build_parser().parse_args(["-i", "2.5"]).interval == 2.5
    assert build_parser().parse_args(["--interval", "0.1"]).interval == 0.1


def test_selftest_takes_two_integers():
    assert build_parser().parse_args(["--selftest", "120", "40"]).selftest == [120, 40]


def test_selftest_rejects_a_single_argument(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--selftest", "120"])
    assert "selftest" in capsys.readouterr().err


def test_version_flag_prints_the_displayed_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"{COMMAND_NAME} {APP_VERSION}"


def test_help_names_the_app(capsys):
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert COMMAND_NAME in out
    assert f"{APP_NAME} {APP_VERSION}" in out
    for flag in ("--interval", "--selftest", "--probe"):
        assert flag in out


# --- commands ----------------------------------------------------------------


@requires_backend
def test_selftest_renders_one_frame(capsys):
    assert main(["--selftest", "120", "40"]) == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "col_mode=full" in out
    assert "row_mode=" in out
    assert "TOTAL" in out
    assert "RAM" in out
    assert out.endswith("\n")


@requires_backend
def test_selftest_reports_a_window_too_small(capsys):
    assert main(["--selftest", "10", "4"]) == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "col_mode=too-narrow" in out
    assert "too small" in out


@requires_backend
def test_probe_describes_the_machine(capsys):
    assert main(["--probe"]) == ExitCode.SUCCESS
    out = capsys.readouterr().out
    for field in ("python", "platform", "backend", "model", "cores/threads", "sample"):
        assert field in out

"""The startup interpreter guard.

The supported window is declared twice on purpose — as `requires-python` in
pyproject.toml, for packaging, and as plain constants in `python_check`, which
must stay importable before anything else and therefore cannot read the toml.
These tests are what keeps the two copies in step.
"""

from __future__ import annotations

import sys

import pytest

from cpumon.cli.python_check import (
    _MAX_VERSION_EXCLUSIVE,
    _MIN_VERSION,
    _REQUIRED,
    ensure_supported_python,
)
from tests.test_version import PYPROJECT, declared_requires_python

Bound = tuple[int, ...]


def parse_range(spec: str) -> tuple[Bound | None, Bound | None]:
    """('>=3.12,<3.13') -> ((3, 12), (3, 13))."""
    lower: Bound | None = None
    upper: Bound | None = None
    for raw in spec.split(","):
        part = raw.strip()
        if part.startswith(">="):
            lower = tuple(int(n) for n in part[2:].split("."))
        elif part.startswith("<"):
            upper = tuple(int(n) for n in part[1:].split("."))
    return lower, upper


@pytest.mark.skipif(not PYPROJECT.exists(), reason="not a source checkout")
def test_declared_range_matches_pyproject():
    assert declared_requires_python() == _REQUIRED


def test_numeric_bounds_match_the_declared_range():
    assert parse_range(_REQUIRED) == (_MIN_VERSION, _MAX_VERSION_EXCLUSIVE)


def test_the_running_interpreter_is_the_supported_one():
    """The suite should be exercising the version the app is shipped for."""
    assert _MIN_VERSION <= sys.version_info[:2] < _MAX_VERSION_EXCLUSIVE


@pytest.mark.parametrize("version", [(3, 12, 0), (3, 12, 2), (3, 12, 99)])
def test_supported_versions_pass(monkeypatch, version):
    monkeypatch.setattr(sys, "version_info", (*version, "final", 0))
    assert ensure_supported_python() is None


@pytest.mark.parametrize("version", [(2, 7, 18), (3, 11, 9), (3, 13, 0), (4, 0, 0)])
def test_unsupported_versions_fail_fast(monkeypatch, capsys, version):
    monkeypatch.setattr(sys, "version_info", (*version, "final", 0))
    with pytest.raises(SystemExit) as excinfo:
        ensure_supported_python()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert _REQUIRED in err
    assert ".".join(str(n) for n in version) in err

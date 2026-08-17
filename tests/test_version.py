"""Tests for the package version wiring.

The declared version lives in pyproject.toml and reaches the app in two ways:
installed distribution metadata, or a file baked into a built artifact. Reading
the toml is fine *here* — a test is not runtime.
"""

from __future__ import annotations

from pathlib import Path
from tomllib import load as toml_load

import pytest

import cpumon
from cpumon.app_info import APP_VERSION

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _declared(field: str) -> str:
    with PYPROJECT.open("rb") as handle:
        return str(toml_load(handle)["project"][field])


def declared_version() -> str:
    return _declared("version")


def declared_requires_python() -> str:
    return _declared("requires-python")


def test_version_is_non_empty_string():
    assert isinstance(cpumon.__version__, str)
    assert cpumon.__version__


def test_the_app_reports_the_package_version():
    assert cpumon.__version__ == APP_VERSION


@pytest.mark.skipif(not PYPROJECT.exists(), reason="not a source checkout")
def test_resolved_version_matches_pyproject():
    assert cpumon.__version__ == declared_version(), (
        "the resolved version is stale: run `poetry install` after bumping "
        "[project].version, or rebuild the artifact"
    )


def test_no_baked_version_in_the_source_tree():
    """_version.py is generated into the archive, never committed."""
    baked = Path(cpumon.__file__).parent / "_version.py"
    assert not baked.exists()

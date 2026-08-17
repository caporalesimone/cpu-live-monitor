"""The shipped artifact: build it, look inside it, run it.

This is the deliverable for a device with nothing but a Python interpreter, so
it is tested the way it will be used — as a subprocess, from a file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.test_version import declared_version

BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "build_zipapp.py"


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("dist") / "cpumon.pyz"
    subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "-o", str(target)],
        check=True,
        capture_output=True,
    )
    return target


def run(archive: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run the archive with a bare environment, as a device would."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONIOENCODING"}
    return subprocess.run(
        [sys.executable, str(archive), *args],
        capture_output=True,
        check=False,
        env=env,
    )


# --- contents ----------------------------------------------------------------


def test_the_archive_is_self_contained(archive: Path):
    names = zipfile.ZipFile(archive).namelist()
    assert "__main__.py" in names
    assert "cpumon/cli/main.py" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in names if n.endswith((".toml", ".lock"))]


def test_the_version_is_baked_in(archive: Path):
    baked = zipfile.ZipFile(archive).read("cpumon/_version.py").decode("utf-8")
    assert f'__version__ = "{declared_version()}"' in baked


def test_it_is_small_enough_to_copy_around(archive: Path):
    assert archive.stat().st_size < 512 * 1024


# --- behaviour ---------------------------------------------------------------


def test_version_flag_reports_the_declared_version(archive: Path):
    result = run(archive, "--version")
    assert result.returncode == 0
    assert result.stdout.decode().strip() == f"cpumon {declared_version()}"


def test_it_renders_a_frame(archive: Path):
    result = run(archive, "--selftest", "120", "40")
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    # Decoded as UTF-8 on purpose: a redirected stdout must not fall back to the
    # locale encoding, which cannot represent the box drawing.
    out = result.stdout.decode("utf-8")
    assert "TOTAL" in out
    assert "RAM" in out
    assert "─" in out


def test_it_probes_the_machine(archive: Path):
    result = run(archive, "--probe")
    assert result.returncode == 0
    assert b"backend" in result.stdout


def test_exit_codes_are_not_swallowed(archive: Path):
    """zipapp's own entry point discards main()'s return value; ours does not."""
    assert run(archive, "--selftest", "120").returncode == 2
    assert run(archive, "--nope").returncode == 2
    assert run(archive, "--help").returncode == 0

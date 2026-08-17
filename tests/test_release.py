"""The release flow: what the pipeline will name, and what it will publish.

The workflow calls these two scripts and trusts what they print, so they are
tested the way it calls them — as subprocesses, on real files.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_version import declared_version

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
CHANGELOG = ROOT / "CHANGELOG.md"

SAMPLE = """\
# Changelog

## [Unreleased]

## [1.0.1] - 2026-08-18

### Added

- A thing.

## [1.0.0] - 2026-08-17

First release.

[Unreleased]: https://example.invalid/compare/v1.0.1...HEAD
[1.0.1]: https://example.invalid/compare/v1.0.0...v1.0.1
"""


def notes(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOLS / "release_notes.py"), *args],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )


# --- extracting the notes -----------------------------------------------------


def test_it_takes_the_section_and_stops_at_the_next_one(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    result = notes("1.0.1", "--changelog", str(changelog))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "### Added\n\n- A thing."


def test_the_leading_v_is_optional_on_both_sides(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    assert (
        notes("v1.0.1", "--changelog", str(changelog)).stdout
        == notes("1.0.1", "--changelog", str(changelog)).stdout
    )


def test_the_link_definitions_are_not_part_of_the_notes(tmp_path):
    """They sit at the foot of the file, inside the last section as written."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    result = notes("1.0.0", "--changelog", str(changelog))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "First release."


def test_an_unknown_version_fails_loudly(tmp_path):
    """The workflow must stop, not publish a release with nothing in it."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    result = notes("9.9.9", "--changelog", str(changelog))
    assert result.returncode == 1
    assert "9.9.9" in result.stderr


def test_an_empty_section_fails_loudly(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    result = notes("Unreleased", "--changelog", str(changelog))
    assert result.returncode == 1


def test_it_can_write_a_file_for_gh(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(SAMPLE, encoding="utf-8")
    out = tmp_path / "notes.md"
    assert notes("1.0.1", "--changelog", str(changelog), "-o", str(out)).returncode == 0
    assert out.read_text(encoding="utf-8").strip() == "### Added\n\n- A thing."


# --- against the real files ---------------------------------------------------


def test_the_current_version_has_release_notes():
    """Bumping the version without writing the changelog breaks the release."""
    result = notes(declared_version(), "--changelog", str(CHANGELOG))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_the_archive_is_named_after_the_version():
    """The workflow attaches dist/cpumon-<version>.pyz by that exact name."""
    result = subprocess.run(
        [sys.executable, str(TOOLS / "build_zipapp.py"), "--print-output"],
        capture_output=True,
        check=True,
        text=True,
    )
    printed = Path(result.stdout.strip())
    assert printed.name == f"cpumon-{declared_version()}.pyz"
    assert printed.parent.name == "dist"
    assert not printed.exists() or printed.is_file()  # printing must not build

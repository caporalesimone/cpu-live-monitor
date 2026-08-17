"""Build a self-contained ``cpumon-<version>.pyz``, using the standard library only.

    python tools/build_zipapp.py

The archive needs nothing on the target device but a CPython interpreter that
satisfies ``requires-python``: no pip, no poetry, no site-packages, no unpacking
step. Copy the file over and run ``python cpumon-1.0.1.pyz``.

The version is read from pyproject.toml *here*, at build time, and baked into
the archive as ``cpumon/_version.py``. That is what keeps pyproject.toml the
single source of truth while the app never reads it at runtime — inside a zipapp
there is no installed distribution metadata to fall back on. It also names the
file, so an archive can be identified without being run, and two of them can sit
side by side on a device.

``--print-output`` reports where the archive will go without building it, so the
release workflow and ``deploy.bat`` never have to spell the naming rule out a
second time.
"""

from __future__ import annotations

import argparse
import shutil
import zipapp
from pathlib import Path
from tempfile import TemporaryDirectory
from tomllib import load as toml_load

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE = ROOT / "src" / "cpumon"
DIST = ROOT / "dist"

# Spelled out rather than taken from __doc__: docstrings are stripped under
# python -OO, which would make the help text vanish or the build crash.
DESCRIPTION = "build a self-contained cpumon-<version>.pyz, using the standard library only"

# A shebang makes the archive directly executable on POSIX after chmod +x, and
# is simply ignored on Windows, where you run `python cpumon.pyz`.
DEFAULT_INTERPRETER = "/usr/bin/env python3"

_BAKED_VERSION = '''\
"""Version baked in at build time by tools/build_zipapp.py.

Generated file: do not edit and do not commit. The source of truth is
[project].version in pyproject.toml.
"""

__version__ = "{version}"
'''

# zipapp's own generated entry point discards the return value of main(), which
# would flatten every exit code to 0. This one does not.
_ENTRY_POINT = '''\
"""Entry point for the zipapp archive."""

import sys

from cpumon.cli.main import main

sys.exit(main())
'''

_IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def read_project() -> tuple[str, str]:
    """(version, requires-python) as declared in pyproject.toml."""
    with PYPROJECT.open("rb") as handle:
        project = toml_load(handle)["project"]
    return str(project["version"]), str(project.get("requires-python", "any"))


def default_output(version: str) -> Path:
    """The name the archive ships under. The one place that rule is stated."""
    return DIST / f"cpumon-{version}.pyz"


def build(output: Path | None = None, interpreter: str | None = DEFAULT_INTERPRETER) -> Path:
    """Write a runnable archive of the package to *output*."""
    version, requires_python = read_project()
    if output is None:
        output = default_output(version)
    with TemporaryDirectory() as tmp:
        staging = Path(tmp) / "archive"
        shutil.copytree(PACKAGE, staging / "cpumon", ignore=_IGNORED)
        (staging / "cpumon" / "_version.py").write_text(
            _BAKED_VERSION.format(version=version), encoding="utf-8"
        )
        (staging / "__main__.py").write_text(_ENTRY_POINT, encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        zipapp.create_archive(staging, target=output, interpreter=interpreter, compressed=True)

    print(f"built    : {output}")
    print(f"version  : {version}")
    print(f"requires : python {requires_python}")
    print(f"size     : {output.stat().st_size / 1024:.1f} KiB")
    print(f"run      : python {output.name}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(prog="build_zipapp", description=DESCRIPTION)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="archive to write (default: dist/cpumon-<version>.pyz)",
    )
    parser.add_argument(
        "--print-output",
        action="store_true",
        help="print the path that would be written, and build nothing",
    )
    parser.add_argument(
        "--interpreter",
        default=DEFAULT_INTERPRETER,
        help="shebang line for POSIX targets (default: %(default)s)",
    )
    parser.add_argument(
        "--no-interpreter",
        action="store_const",
        const=None,
        dest="interpreter",
        help="omit the shebang entirely",
    )
    args = parser.parse_args()
    if args.print_output:
        print(args.output or default_output(read_project()[0]))
        return 0
    build(args.output, args.interpreter)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Identity of the application as it appears on screen.

The version is not spelled out here: it is the installed distribution version,
resolved by :mod:`cpumon.version` from metadata generated out of
``[project].version``. Bumping the package therefore bumps what the title bar,
the help page and ``--version`` all report, with no second place to keep in
step, and without the app reading pyproject.toml at runtime.
"""

from __future__ import annotations

from typing import Final

from cpumon.version import __version__

APP_NAME: Final[str] = "CPU LIVE MONITOR"
APP_NAME_SHORT: Final[str] = "CPUMON"
APP_VERSION: Final[str] = __version__
APP_AUTHOR: Final[str] = "Caporale Simone"
APP_YEAR: Final[str] = "2026"

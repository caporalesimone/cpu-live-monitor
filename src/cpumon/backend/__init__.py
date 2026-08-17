"""Platform backends: one module per operating system, behind one interface.

Every OS specific call in the program lives in exactly one of these modules:

    base.py     the three abstract interfaces every platform implements
    windows.py  ntdll / kernel32 / msvcrt / winreg
    linux.py    /proc, /sys, termios

:func:`create_backend` is the only way the rest of the app obtains one. The
platform modules are imported lazily, because each imports OS specific
libraries at module level and loading the wrong one would fail outright.
"""

from __future__ import annotations

import sys

from cpumon.backend.base import CpuSampler, PlatformBackend, TerminalBackend
from cpumon.core.errors import PlatformError

__all__ = [
    "CpuSampler",
    "PlatformBackend",
    "TerminalBackend",
    "create_backend",
]


def create_backend() -> PlatformBackend:
    """Return the backend for the running platform."""
    if sys.platform == "win32":
        from cpumon.backend.windows import WindowsBackend

        return WindowsBackend()
    if sys.platform.startswith("linux"):
        from cpumon.backend.linux import LinuxBackend

        return LinuxBackend()
    raise PlatformError(f"unsupported platform: {sys.platform}")

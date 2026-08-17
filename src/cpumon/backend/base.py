"""Abstract platform API.

Porting to a new platform means implementing these three interfaces and
registering the backend in :mod:`cpumon.backend.factory`.
"""

from __future__ import annotations

import shutil
import sys
from abc import ABC, abstractmethod

from cpumon.core.model import MemoryInfo, Topology


class CpuSampler(ABC):
    """Produces per-logical-processor busy percentages."""

    @abstractmethod
    def count(self) -> int:
        """Number of logical processors this sampler reports on."""

    @abstractmethod
    def sample(self) -> list[float]:
        """Busy percentage per logical processor since the previous call.

        The first call after construction primes the deltas and returns zeros.
        """

    def close(self) -> None:  # noqa: B027 - optional hook, not every sampler holds one
        """Release any OS resource held by the sampler."""


class TerminalBackend(ABC):
    """Raw terminal access: size, output, and normalised key input."""

    @abstractmethod
    def setup(self) -> None:
        """Put the terminal into the mode the renderer expects."""

    @abstractmethod
    def teardown(self) -> None:
        """Restore whatever :meth:`setup` changed."""

    @abstractmethod
    def read_key(self, timeout: float) -> str | None:
        """Block up to *timeout* seconds. Returns a normalised key name.

        Normalised names: 'F1'..'F12', 'UP', 'DOWN', 'PGUP', 'PGDN', 'HOME',
        'END', 'ESC', 'CTRL_C', or the literal character for ordinary keys.
        Returns None on timeout.
        """

    def size(self) -> tuple[int, int]:
        cols, rows = shutil.get_terminal_size(fallback=(80, 24))
        return cols, rows

    def write(self, text: str) -> None:
        sys.stdout.write(text)

    def flush(self) -> None:
        sys.stdout.flush()


class PlatformBackend(ABC):
    """Bundles the platform specific pieces."""

    name: str = "generic"

    @abstractmethod
    def read_topology(self) -> Topology: ...

    @abstractmethod
    def create_sampler(self) -> CpuSampler: ...

    @abstractmethod
    def create_terminal(self) -> TerminalBackend: ...

    @abstractmethod
    def uptime_seconds(self) -> float: ...

    @abstractmethod
    def read_memory(self) -> MemoryInfo: ...

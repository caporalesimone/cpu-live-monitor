"""Shared builders for tests that need a machine without having one."""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Sequence

import pytest

from cpumon.backend.base import CpuSampler, PlatformBackend, TerminalBackend
from cpumon.core.collectors import CpuCollector
from cpumon.core.history import HistoryStore, SeriesKey
from cpumon.core.model import CoreClass, MemoryInfo, Topology
from cpumon.core.topology import build_topology
from cpumon.settings import HISTORY_CAPACITY

# 6 P cores with SMT plus 8 E cores: the shape that exercises every branch.
HYBRID_SPEC: list[tuple[CoreClass, list[int]]] = [
    *[(CoreClass.P, [2 * i, 2 * i + 1]) for i in range(6)],
    *[(CoreClass.E, [12 + i]) for i in range(8)],
]
UNIFORM_SPEC: list[tuple[CoreClass, list[int]]] = [
    (CoreClass.P, [2 * i, 2 * i + 1]) for i in range(4)
]

_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ROW_START = re.compile(r"\x1b\[(\d+);1H\x1b\[2K")


class FakeSampler(CpuSampler):
    """Returns a fixed pattern, so aggregates are predictable."""

    def __init__(self, values: Sequence[float]) -> None:
        self._values = list(values)
        self.calls = 0

    def count(self) -> int:
        return len(self._values)

    def sample(self) -> list[float]:
        self.calls += 1
        return list(self._values)


class ScriptedTerminal(TerminalBackend):
    """A terminal that types a fixed script and remembers what was drawn."""

    def __init__(
        self,
        keys: Sequence[str] = (),
        sizes: Sequence[tuple[int, int]] = ((120, 40),),
        key_delay: float = 0.05,
    ) -> None:
        self._keys = deque(keys)
        self._sizes = deque(sizes)
        self._key_delay = key_delay
        self.written: list[str] = []
        self.setup_calls = 0
        self.teardown_calls = 0

    def setup(self) -> None:
        self.setup_calls += 1

    def teardown(self) -> None:
        self.teardown_calls += 1

    def read_key(self, timeout: float) -> str | None:
        if self._keys:
            # Spaced out, so the render loop sees one key at a time.
            time.sleep(min(self._key_delay, timeout))
            return self._keys.popleft()
        time.sleep(timeout)
        return None

    def size(self) -> tuple[int, int]:
        current = self._sizes[0]
        if len(self._sizes) > 1:
            self._sizes.popleft()  # the next read reports a resized window
        return current

    def write(self, text: str) -> None:
        self.written.append(text)

    def flush(self) -> None:
        pass


class StubBackend(PlatformBackend):
    """A whole machine, invented."""

    name = "stub"

    def __init__(
        self,
        topology: Topology | None = None,
        sampler: CpuSampler | None = None,
        memory: MemoryInfo | None = None,
        terminal: TerminalBackend | None = None,
    ) -> None:
        self._topology = topology or build_topology("Stub CPU", HYBRID_SPEC)
        self._sampler = sampler or FakeSampler([1.0] * self._topology.n_cpus)
        self._memory = memory or MemoryInfo(total=32 * 2**30, available=16 * 2**30)
        self._terminal = terminal or ScriptedTerminal()

    def read_topology(self) -> Topology:
        return self._topology

    def create_sampler(self) -> CpuSampler:
        return self._sampler

    def create_terminal(self) -> TerminalBackend:
        return self._terminal

    def uptime_seconds(self) -> float:
        return 4242.0

    def read_memory(self) -> MemoryInfo:
        return self._memory


@pytest.fixture
def hybrid_topology() -> Topology:
    return build_topology("Fake Hybrid CPU", HYBRID_SPEC)


@pytest.fixture
def uniform_topology() -> Topology:
    return build_topology("Fake Uniform CPU", UNIFORM_SPEC)


@pytest.fixture
def memory() -> MemoryInfo:
    return MemoryInfo(total=32 * 2**30, available=12 * 2**30)


@pytest.fixture
def memory_with_swap() -> MemoryInfo:
    return MemoryInfo(
        total=16 * 2**30,
        available=3 * 2**30,
        backing_kind="swap",
        backing_total=8 * 2**30,
        backing_used=2 * 2**30,
    )


def all_series_keys(topology: Topology) -> list[str]:
    """Every key the collectors would register for this machine."""
    cpu = CpuCollector(FakeSampler([0.0] * topology.n_cpus), topology)
    return [*cpu.series_keys(), SeriesKey.MEMORY, SeriesKey.BACKING]


def filled_history(
    keys: Sequence[str], count: int = 60, marks: Sequence[tuple[int, str]] = ()
) -> HistoryStore:
    """A store holding *count* deterministic samples for every key in *keys*."""
    history = HistoryStore(HISTORY_CAPACITY)
    history.ensure_many(keys)
    pinned = dict(marks)
    for i in range(count):
        if i in pinned:
            history.mark(pinned[i])
        history.push(
            {key: ((i * 7 + sum(ord(c) for c in key)) % 1001) / 10.0 for key in keys},
            at=1000.0 + i * 0.75,
        )
    return history


def plain(text: str) -> str:
    """Drop every escape sequence, leaving what the eye actually sees."""
    return _ESCAPE.sub("", text)


def visible_rows(frame: str) -> dict[int, str]:
    """Map 0-based terminal row -> plain text written to it."""
    rows: dict[int, str] = {}
    parts = _ROW_START.split(frame)
    # parts alternates: [prefix, row_number, content, row_number, content, ...]
    for i in range(1, len(parts) - 1, 2):
        rows[int(parts[i]) - 1] = plain(parts[i + 1])
    return rows

"""Platform-neutral description of the CPU and of memory.

Backends produce these types; every layer above consumes only these types and
never touches OS specifics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class CoreClass(StrEnum):
    """Performance class of a physical core, ordered best to worst."""

    P = "P"  # performance core
    E = "E"  # efficiency core
    LPE = "LPE"  # low-power efficiency core (SoC tile)
    UNKNOWN = "?"


@dataclass(frozen=True)
class LogicalCpu:
    """One logical processor (one hardware thread)."""

    index: int  # index into the sampler's value array
    lp_id: int  # OS logical processor id
    core_id: int  # index of the owning physical core
    core_class: CoreClass
    smt_index: int  # 0 for the primary thread of the core

    @property
    def type_tag(self) -> str:
        """Short label, at most 4 characters, shown in the TYPE column."""
        base = self.core_class.value
        if self.smt_index == 0:
            return base
        return (base + "HT")[:4]


@dataclass(frozen=True)
class PhysicalCore:
    """One physical core and the logical processors it owns."""

    core_id: int
    core_class: CoreClass
    cpus: tuple[LogicalCpu, ...]

    @property
    def label(self) -> str:
        """Compact id list, e.g. '0/1' for an SMT pair."""
        return "/".join(str(c.lp_id) for c in self.cpus)


@dataclass(frozen=True)
class MemoryInfo:
    """Memory snapshot in bytes, physical plus one backing-store metric.

    ``backing_kind`` names the secondary metric, or is empty when there is
    nothing worth showing. Today only Linux fills it in, with "swap": pages
    actually written out to swap devices.

    Windows deliberately reports nothing here. Its natural analogue is the
    commit charge, which GlobalMemoryStatusEx already hands us for free, but
    with a system-managed pagefile the commit limit grows under pressure, so
    the ratio is engineered never to look alarming and tracks the RAM row
    closely. It would cost a screen row to say almost nothing. Pagefile
    occupancy is no better: Windows writes there proactively even with RAM to
    spare, so a high value does not mean the machine is struggling. The
    figure that would earn its place is hard faults per second — see the note
    on SystemPerformanceInformation in the Windows backend.
    """

    BACKING_ROW_LABEL: ClassVar[str] = "SWAP"

    total: int
    available: int
    backing_kind: str = ""
    backing_total: int = 0
    backing_used: int = 0

    @property
    def used(self) -> int:
        return max(0, self.total - self.available)

    @property
    def percent(self) -> float:
        return 100.0 * self.used / self.total if self.total else 0.0

    @property
    def has_backing(self) -> bool:
        return bool(self.backing_kind) and self.backing_total > 0

    @property
    def backing_percent(self) -> float:
        if not self.backing_total:
            return 0.0
        return 100.0 * self.backing_used / self.backing_total


@dataclass(frozen=True)
class Topology:
    """The whole machine: its name, its threads and its cores."""

    model_name: str
    cpus: tuple[LogicalCpu, ...]
    cores: tuple[PhysicalCore, ...]

    @property
    def n_cpus(self) -> int:
        return len(self.cpus)

    @property
    def n_cores(self) -> int:
        return len(self.cores)

    @property
    def classes(self) -> tuple[CoreClass, ...]:
        """Distinct core classes, ordered best first."""
        order = (CoreClass.P, CoreClass.E, CoreClass.LPE, CoreClass.UNKNOWN)
        present = {c.core_class for c in self.cores}
        return tuple(k for k in order if k in present)

    @property
    def hybrid(self) -> bool:
        return len(self.classes) > 1

    def cpus_of_class(self, klass: CoreClass) -> tuple[LogicalCpu, ...]:
        return tuple(c for c in self.cpus if c.core_class is klass)

    def cores_of_class(self, klass: CoreClass) -> tuple[PhysicalCore, ...]:
        return tuple(c for c in self.cores if c.core_class is klass)

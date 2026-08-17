"""Assembly and folding of the CPU topology.

:func:`build_topology` is the single place where index and SMT bookkeeping
happens, so every backend agrees on it. :func:`core_buckets` is the single
place where cores are folded into screen rows, so the renderer (which draws
the labels) and the collector (which feeds the series) can never disagree
about which cores belong to which row.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from cpumon.core.model import CoreClass, LogicalCpu, PhysicalCore, Topology

# Bucket sizes tried, in order, when folding cores to save vertical space.
GROUP_SIZES: Final[tuple[int, ...]] = (1, 2, 3, 4, 6, 8, 12, 16)


def build_topology(
    model_name: str, raw_cores: Sequence[tuple[CoreClass, Sequence[int]]]
) -> Topology:
    """Turn (class, logical processor ids) pairs into a :class:`Topology`.

    The sampler indexes its result array by position, so the model must map
    onto a contiguous 0..n-1 range: logical processors are sorted by OS id and
    numbered accordingly. The caller validates that assumption against the
    sampler it actually got.
    """
    ordered = sorted(raw_cores, key=lambda item: min(item[1]))

    # Pass one: group the ids per core and remember which core each id is on.
    core_classes: list[CoreClass] = []
    members_per_core: list[list[tuple[int, int]]] = []  # (lp_id, smt_index)
    for klass, lp_ids in ordered:
        core_classes.append(klass)
        members_per_core.append(list(enumerate(sorted(lp_ids))))

    # Pass two: number every logical processor by ascending OS id, which is the
    # order the samplers report their values in.
    index_of = {
        lp_id: index
        for index, lp_id in enumerate(
            sorted(lp_id for members in members_per_core for _smt, lp_id in members)
        )
    }

    cores: list[PhysicalCore] = []
    for core_id, (klass, members) in enumerate(zip(core_classes, members_per_core, strict=True)):
        cores.append(
            PhysicalCore(
                core_id=core_id,
                core_class=klass,
                cpus=tuple(
                    LogicalCpu(
                        index=index_of[lp_id],
                        lp_id=lp_id,
                        core_id=core_id,
                        core_class=klass,
                        smt_index=smt_index,
                    )
                    for smt_index, lp_id in members
                ),
            )
        )

    cpus = tuple(sorted((cpu for core in cores for cpu in core.cpus), key=lambda c: c.lp_id))
    return Topology(model_name, cpus, tuple(cores))


def core_buckets(
    topology: Topology, group_size: int
) -> list[tuple[CoreClass, list[list[PhysicalCore]]]]:
    """Split each performance class into buckets of at most *group_size* cores."""
    out: list[tuple[CoreClass, list[list[PhysicalCore]]]] = []
    for klass in topology.classes:
        members = list(topology.cores_of_class(klass))
        buckets = [members[i : i + group_size] for i in range(0, len(members), group_size)]
        out.append((klass, buckets))
    return out


def bucket_label(cores: Sequence[PhysicalCore]) -> str:
    """Compact identifier for a row covering one or more physical cores."""
    if len(cores) == 1:
        return cores[0].label
    ids = [c.lp_id for core in cores for c in core.cpus]
    return f"{min(ids)}-{max(ids)}"

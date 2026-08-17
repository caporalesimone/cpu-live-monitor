"""Topology assembly: the sampler indexes by position, so numbering matters."""

from __future__ import annotations

from cpumon.core.model import CoreClass, Topology
from cpumon.core.topology import bucket_label, build_topology, core_buckets
from tests.conftest import HYBRID_SPEC, UNIFORM_SPEC


def test_indices_are_contiguous_and_follow_os_ids() -> None:
    topo = build_topology("x", HYBRID_SPEC)
    assert [c.index for c in topo.cpus] == list(range(topo.n_cpus))
    assert [c.lp_id for c in topo.cpus] == sorted(c.lp_id for c in topo.cpus)
    for expected, cpu in enumerate(topo.cpus):
        assert cpu.index == expected


def test_indices_hold_when_the_input_is_out_of_order() -> None:
    shuffled = [
        (CoreClass.E, [13]),
        (CoreClass.P, [2, 3]),
        (CoreClass.E, [12]),
        (CoreClass.P, [0, 1]),
    ]
    topo = build_topology("x", shuffled)
    # Cores are renumbered by their lowest logical processor id.
    assert [(c.core_id, c.label) for c in topo.cores] == [
        (0, "0/1"),
        (1, "2/3"),
        (2, "12"),
        (3, "13"),
    ]
    assert [c.index for c in topo.cpus] == [0, 1, 2, 3, 4, 5]


def test_smt_siblings_are_tagged() -> None:
    topo = build_topology("x", UNIFORM_SPEC)
    tags = [c.type_tag for c in topo.cpus]
    assert tags == ["P", "PHT"] * 4
    assert all(len(tag) <= 4 for tag in tags)


def test_type_tag_truncates_to_four_characters() -> None:
    topo = build_topology("x", [(CoreClass.LPE, [0, 1])])
    assert [c.type_tag for c in topo.cpus] == ["LPE", "LPEH"]


def test_class_ordering_is_best_first() -> None:
    topo = build_topology("x", [(CoreClass.E, [1]), (CoreClass.LPE, [2]), (CoreClass.P, [0])])
    assert topo.classes == (CoreClass.P, CoreClass.E, CoreClass.LPE)
    assert topo.hybrid is True


def test_uniform_machine_is_not_hybrid() -> None:
    topo = build_topology("x", UNIFORM_SPEC)
    assert topo.classes == (CoreClass.P,)
    assert topo.hybrid is False


def test_core_buckets_cover_every_core_exactly_once(hybrid_topology: Topology) -> None:
    for size in (1, 2, 3, 4, 8, 16):
        seen = [
            core.core_id
            for _klass, buckets in core_buckets(hybrid_topology, size)
            for bucket in buckets
            for core in bucket
        ]
        assert sorted(seen) == [c.core_id for c in hybrid_topology.cores]


def test_core_buckets_never_mix_classes(hybrid_topology: Topology) -> None:
    for klass, buckets in core_buckets(hybrid_topology, 4):
        for bucket in buckets:
            assert {core.core_class for core in bucket} == {klass}
            assert len(bucket) <= 4


def test_bucket_label(hybrid_topology: Topology) -> None:
    cores = hybrid_topology.cores
    assert bucket_label(cores[:1]) == "0/1"  # a single SMT core keeps its pair
    assert bucket_label(cores[:3]) == "0-5"  # a group states its id range

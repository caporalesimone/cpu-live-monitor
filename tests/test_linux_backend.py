"""The Linux backend, against fake sysfs and procfs trees.

Every path the backend reads is injectable, so these run on any platform. The
cases are real machines: an x86 SMT part, a hybrid Intel part, an ARM cluster
with no SMT at all, and kernels that report less than the full picture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpumon.backend.linux import LinuxBackend, LinuxSampler, _parse_cpu_list
from cpumon.core.errors import PlatformError
from cpumon.core.model import CoreClass


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def make_sysfs(
    root: Path,
    online: str,
    *,
    siblings: dict[int, str] | None = None,
    core_ids: dict[int, int] | None = None,
    packages: dict[int, int] | None = None,
    capacities: dict[int, int] | None = None,
    types: dict[str, str] | None = None,
    sibling_file: str = "core_cpus_list",
    smt_active: str | None = None,
) -> Path:
    """A sysfs CPU tree exposing exactly the files a given kernel would."""
    sys_cpu = root / "sys" / "devices" / "system" / "cpu"
    write(sys_cpu / "online", online)
    if smt_active is not None:
        write(sys_cpu / "smt" / "active", smt_active)
    for cpu in _parse_cpu_list(online):
        topology = sys_cpu / f"cpu{cpu}" / "topology"
        if siblings is not None:
            write(topology / sibling_file, siblings[cpu])
        if core_ids is not None:
            write(topology / "core_id", str(core_ids[cpu]))
        if packages is not None:
            write(topology / "physical_package_id", str(packages[cpu]))
        if capacities is not None:
            write(sys_cpu / f"cpu{cpu}" / "cpu_capacity", str(capacities[cpu]))
    for name, cpulist in (types or {}).items():
        write(sys_cpu / "types" / name / "cpulist", cpulist)
    return sys_cpu


def make_proc(root: Path, *, cpuinfo: str = "", stat: str = "", meminfo: str = "") -> Path:
    proc = root / "proc"
    write(proc / "cpuinfo", cpuinfo)
    write(proc / "stat", stat)
    write(proc / "meminfo", meminfo)
    return proc


# --- the ARM case that started this ------------------------------------------

# 4x Cortex-A53, one cluster, no SMT. The kernel numbers the cores 0 and 1 twice
# because core_id is only unique within a package, and reports no package id at
# all — the combination that used to be read as two SMT pairs.
ARM_A53 = {
    "online": "0-3",
    "core_ids": {0: 0, 1: 1, 2: 0, 3: 1},
}


def test_a_four_core_arm_part_is_four_cores(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(tmp_path, ARM_A53["online"], siblings={0: "0", 1: "1", 2: "2", 3: "3"})
    backend = LinuxBackend(sys_cpu, make_proc(tmp_path))
    topo = backend.read_topology()

    assert topo.n_cores == 4
    assert topo.n_cpus == 4
    assert [c.type_tag for c in topo.cpus] == ["P", "P", "P", "P"]
    assert "PHT" not in [c.type_tag for c in topo.cpus]


def test_repeated_core_ids_without_a_package_id_do_not_invent_smt(
    tmp_path: Path,
) -> None:
    """The old failure mode: 4 real cores reported as 2 cores + 2 SMT siblings."""
    sys_cpu = make_sysfs(
        tmp_path,
        ARM_A53["online"],
        core_ids=ARM_A53["core_ids"],  # type: ignore[arg-type]
    )
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 4
    assert [c.type_tag for c in topo.cpus] == ["P"] * 4


def test_repeated_core_ids_with_a_package_id_are_grouped_per_cluster(
    tmp_path: Path,
) -> None:
    """Same core_ids, but now the kernel says which cluster each belongs to."""
    sys_cpu = make_sysfs(
        tmp_path,
        "0-3",
        core_ids={0: 0, 1: 1, 2: 0, 3: 1},
        packages={0: 0, 1: 0, 2: 1, 3: 1},
    )
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 4
    assert [c.type_tag for c in topo.cpus] == ["P"] * 4


def test_a_kernel_stating_smt_is_off_vetoes_the_guess(tmp_path: Path) -> None:
    """No sibling lists, repeated core_ids, but the kernel says SMT is off."""
    sys_cpu = make_sysfs(
        tmp_path,
        "0-3",
        core_ids={0: 0, 1: 1, 2: 0, 3: 1},
        packages=dict.fromkeys(range(4), 0),
        smt_active="0",
    )
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 4
    assert [c.type_tag for c in topo.cpus] == ["P"] * 4


def test_a_kernel_stating_smt_is_on_allows_the_guess(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(
        tmp_path,
        "0-3",
        core_ids={0: 0, 1: 0, 2: 1, 3: 1},
        packages=dict.fromkeys(range(4), 0),
        smt_active="1",
    )
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 2
    assert [c.type_tag for c in topo.cpus] == ["P", "PHT", "P", "PHT"]


# --- x86 -----------------------------------------------------------------------


def test_smt_pairs_are_detected_from_the_sibling_list(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(tmp_path, "0-3", siblings={0: "0,1", 1: "0,1", 2: "2,3", 3: "2,3"})
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 2
    assert topo.n_cpus == 4
    assert [c.type_tag for c in topo.cpus] == ["P", "PHT", "P", "PHT"]
    assert [core.label for core in topo.cores] == ["0/1", "2/3"]


def test_the_older_sibling_file_is_read_too(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(
        tmp_path,
        "0-3",
        siblings={0: "0,1", 1: "0,1", 2: "2,3", 3: "2,3"},
        sibling_file="thread_siblings_list",
    )
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 2
    assert [c.type_tag for c in topo.cpus] == ["P", "PHT", "P", "PHT"]


def test_ranges_in_the_sibling_list(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(tmp_path, "0-3", siblings=dict.fromkeys(range(4), "0-3"))
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 1
    assert topo.cores[0].label == "0/1/2/3"


def test_offline_siblings_are_ignored(tmp_path: Path) -> None:
    """A sibling list names every CPU of the core, online or not."""
    sys_cpu = make_sysfs(tmp_path, "0,2", siblings={0: "0,1", 2: "2,3"})
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 2
    assert [c.lp_id for c in topo.cpus] == [0, 2]
    assert [c.type_tag for c in topo.cpus] == ["P", "P"]


def test_hybrid_classes_come_from_the_types_directory(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(
        tmp_path,
        "0-5",
        siblings={0: "0,1", 1: "0,1", 2: "2,3", 3: "2,3", 4: "4", 5: "5"},
        types={"intel_core": "0-3", "intel_atom": "4-5"},
    )
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.hybrid is True
    assert topo.classes == (CoreClass.P, CoreClass.E)
    assert [c.type_tag for c in topo.cpus] == ["P", "PHT", "P", "PHT", "E", "E"]


def test_arm_big_little_falls_back_to_cpu_capacity(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(
        tmp_path,
        "0-3",
        siblings={0: "0", 1: "1", 2: "2", 3: "3"},
        capacities={0: 1024, 1: 1024, 2: 462, 3: 462},
    )
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.hybrid is True
    assert [c.type_tag for c in topo.cpus] == ["P", "P", "E", "E"]


def test_uniform_capacities_are_not_hybrid(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(
        tmp_path,
        "0-1",
        siblings={0: "0", 1: "1"},
        capacities={0: 1024, 1: 1024},
    )
    assert LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology().hybrid is False


def test_a_kernel_that_reports_nothing_gives_one_core_per_cpu(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(tmp_path, "0-3")  # no topology directory at all
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cores == 4
    assert [c.type_tag for c in topo.cpus] == ["P"] * 4


def test_a_missing_online_file_assumes_cpu0(tmp_path: Path) -> None:
    """Old kernels do not expose it, and every machine has at least one CPU."""
    sys_cpu = make_sysfs(tmp_path, "0")
    (sys_cpu / "online").unlink()
    topo = LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()
    assert topo.n_cpus == 1
    assert topo.cpus[0].lp_id == 0


def test_an_unparsable_online_list_is_refused(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(tmp_path, "0")
    (sys_cpu / "online").write_text(",,", encoding="ascii")
    with pytest.raises(PlatformError, match="no online CPUs"):
        LinuxBackend(sys_cpu, make_proc(tmp_path)).read_topology()


# --- the model name ------------------------------------------------------------


def test_x86_takes_its_name_from_cpuinfo(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(tmp_path, "0", siblings={0: "0"})
    proc = make_proc(tmp_path, cpuinfo="processor : 0\nmodel name : Fancy CPU 9000\n")
    assert LinuxBackend(sys_cpu, proc).read_topology().model_name == "Fancy CPU 9000"


def test_arm_falls_back_to_the_device_tree(tmp_path: Path) -> None:
    """ARM /proc/cpuinfo has no model name; a board has a device-tree one."""
    sys_cpu = make_sysfs(tmp_path, "0", siblings={0: "0"})
    proc = make_proc(tmp_path, cpuinfo="processor : 0\nCPU part : 0xd03\n")
    write(proc / "device-tree" / "model", "Mercury Gen3 Board\x00")
    assert LinuxBackend(sys_cpu, proc).read_topology().model_name == "Mercury Gen3 Board"


# --- /proc/stat ----------------------------------------------------------------

STAT_IDLE = """\
cpu  100 0 100 800 0 0 0 0 0 0
cpu0 50 0 50 400 0 0 0 0 0 0
cpu1 50 0 50 400 0 0 0 0 0 0
intr 12345
"""
STAT_BUSY = """\
cpu  200 0 200 900 0 0 0 0 0 0
cpu0 150 0 50 400 0 0 0 0 0 0
cpu1 50 0 50 500 0 0 0 0 0 0
intr 12346
"""


def test_the_sampler_counts_only_per_cpu_rows(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    write(stat, STAT_IDLE)
    sampler = LinuxSampler(stat)
    assert sampler.count() == 2


def test_the_first_sample_primes_the_deltas(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    write(stat, STAT_IDLE)
    sampler = LinuxSampler(stat)
    assert sampler.sample() == [0.0, 0.0]  # nothing changed between reads


def test_busy_share_is_the_non_idle_delta(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    write(stat, STAT_IDLE)
    sampler = LinuxSampler(stat)
    write(stat, STAT_BUSY)
    # cpu0: +100 busy, +0 idle -> 100%.  cpu1: +0 busy, +100 idle -> 0%.
    assert sampler.sample() == [100.0, 0.0]


def test_iowait_counts_as_idle(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    write(stat, "cpu  0 0 0 0 0 0 0 0\ncpu0 0 0 0 0 0 0 0 0\n")
    sampler = LinuxSampler(stat)
    write(stat, "cpu  0 0 0 0 100 0 0 0\ncpu0 0 0 0 0 100 0 0 0\n")
    assert sampler.sample() == [0.0]


def test_an_empty_stat_is_refused(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    write(stat, "intr 0\n")
    with pytest.raises(PlatformError, match="no per-cpu rows"):
        LinuxSampler(stat)


# --- /proc/meminfo and /proc/uptime -------------------------------------------

MEMINFO = """\
MemTotal:       16000000 kB
MemFree:         1000000 kB
MemAvailable:    8000000 kB
SwapTotal:       4000000 kB
SwapFree:        3000000 kB
"""


def test_memory_is_read_in_bytes(tmp_path: Path) -> None:
    sys_cpu = make_sysfs(tmp_path, "0", siblings={0: "0"})
    proc = make_proc(tmp_path, meminfo=MEMINFO)
    info = LinuxBackend(sys_cpu, proc).read_memory()
    assert info.total == 16_000_000 * 1024
    assert info.available == 8_000_000 * 1024
    assert info.percent == pytest.approx(50.0)


def test_swap_becomes_the_backing_row(tmp_path: Path) -> None:
    proc = make_proc(tmp_path, meminfo=MEMINFO)
    info = LinuxBackend(make_sysfs(tmp_path, "0", siblings={0: "0"}), proc).read_memory()
    assert info.has_backing is True
    assert info.backing_kind == "swap"
    assert info.backing_used == 1_000_000 * 1024  # total - free
    assert info.backing_percent == pytest.approx(25.0)


def test_no_swap_means_no_backing_row(tmp_path: Path) -> None:
    proc = make_proc(
        tmp_path, meminfo="MemTotal: 100 kB\nMemAvailable: 50 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    )
    info = LinuxBackend(make_sysfs(tmp_path, "0", siblings={0: "0"}), proc).read_memory()
    assert info.has_backing is False


def test_unreadable_meminfo_is_reported(tmp_path: Path) -> None:
    backend = LinuxBackend(make_sysfs(tmp_path, "0", siblings={0: "0"}), tmp_path / "missing")
    with pytest.raises(PlatformError, match="cannot read"):
        backend.read_memory()


def test_uptime_is_the_first_field(tmp_path: Path) -> None:
    proc = make_proc(tmp_path)
    write(proc / "uptime", "12345.67 98765.43\n")
    assert LinuxBackend(make_sysfs(tmp_path, "0"), proc).uptime_seconds() == 12345.67


def test_a_missing_uptime_reads_as_zero(tmp_path: Path) -> None:
    assert LinuxBackend(make_sysfs(tmp_path, "0"), tmp_path / "nope").uptime_seconds() == 0.0


# --- cpulist parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("0", [0]),
        ("0-3", [0, 1, 2, 3]),
        ("0-3,8", [0, 1, 2, 3, 8]),
        ("0,2,4", [0, 2, 4]),
        ("0-1,10-11", [0, 1, 10, 11]),
        ("", []),
        (" 0 , 1 ", [0, 1]),
    ],
)
def test_parse_cpu_list(spec: str, expected: list[int]) -> None:
    assert _parse_cpu_list(spec) == expected

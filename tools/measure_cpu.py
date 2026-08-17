"""Measure what one process actually costs, reading /proc and nothing else.

Built for an embedded board: no pidstat, no `top -p`, no psutil, no third-party
modules — just the two counters the kernel already keeps.

    python3 tools/measure_cpu.py --match cpumon.pyz
    python3 tools/measure_cpu.py --pid 1234 --seconds 30
    python3 tools/measure_cpu.py --match cpumon --threads

CPU time comes from fields 14 and 15 of /proc/<pid>/stat (utime, stime) in clock
ticks; the difference over a measured wall-clock interval is the share of one
core the process used. Divided by the number of online CPUs, it is the share of
the whole machine — which is the figure that matters when you are deciding
whether a monitor is worth its own overhead.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROC = Path("/proc")


def clock_ticks_per_second() -> int:
    """USER_HZ, the unit of /proc/<pid>/stat times. 100 on every Linux port."""
    sysconf = getattr(os, "sysconf", None)
    if sysconf is None:  # not a Linux host; nothing else here would work either
        return 100
    return int(sysconf("SC_CLK_TCK"))


def cpu_count() -> int:
    return os.cpu_count() or 1


def _cpu_ticks(stat_line: str) -> tuple[int, int]:
    """(utime, stime) from a /proc/.../stat line.

    The command name is field 2 and may itself contain spaces and parentheses,
    so everything up to the last ')' is skipped rather than split.
    """
    _comm, _, rest = stat_line.rpartition(")")
    fields = rest.split()
    # rest starts at field 3 (state), so utime is index 11 and stime index 12.
    return int(fields[11]), int(fields[12])


def read_process(pid: int) -> tuple[int, int]:
    return _cpu_ticks((PROC / str(pid) / "stat").read_text(encoding="ascii"))


def read_threads(pid: int) -> dict[int, tuple[str, int, int]]:
    """tid -> (name, utime, stime) for every thread of the process."""
    out: dict[int, tuple[str, int, int]] = {}
    for task in sorted((PROC / str(pid) / "task").iterdir(), key=lambda p: int(p.name)):
        try:
            line = (task / "stat").read_text(encoding="ascii")
            name = (task / "comm").read_text(encoding="ascii").strip()
        except OSError:  # the thread ended while we looked
            continue
        utime, stime = _cpu_ticks(line)
        out[int(task.name)] = (name, utime, stime)
    return out


def read_rss_kb(pid: int) -> int:
    try:
        for line in (PROC / str(pid) / "status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        pass
    return 0


def find_pids(needle: str) -> list[int]:
    """Every process whose command line mentions *needle*, excluding this one."""
    found: list[int] = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if needle in cmdline:
            found.append(int(entry.name))
    return sorted(found)


@dataclass(frozen=True)
class Usage:
    seconds: float
    ticks: int
    cores: int
    hz: int

    @property
    def of_one_core(self) -> float:
        return 100.0 * (self.ticks / self.hz) / self.seconds if self.seconds else 0.0

    @property
    def of_machine(self) -> float:
        return self.of_one_core / self.cores


def measure(pid: int, seconds: float, hz: int, cores: int) -> Usage:
    start_ticks = sum(read_process(pid))
    start = time.monotonic()
    time.sleep(seconds)
    elapsed = time.monotonic() - start
    return Usage(elapsed, sum(read_process(pid)) - start_ticks, cores, hz)


def measure_threads(pid: int, seconds: float, hz: int, cores: int) -> list[tuple[str, int, Usage]]:
    before = read_threads(pid)
    start = time.monotonic()
    time.sleep(seconds)
    elapsed = time.monotonic() - start
    after = read_threads(pid)

    rows: list[tuple[str, int, Usage]] = []
    for tid, (name, utime, stime) in after.items():
        was = before.get(tid)
        used = (utime + stime) - (sum(was[1:]) if was else 0)
        rows.append((name, tid, Usage(elapsed, used, cores, hz)))
    return sorted(rows, key=lambda row: -row[2].ticks)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="measure_cpu", description="CPU and memory cost of one process, from /proc"
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pid", type=int, help="process id to watch")
    target.add_argument("--match", help="substring of the command line to find")
    parser.add_argument(
        "-s", "--seconds", type=float, default=10.0, help="sampling window (default: 10)"
    )
    parser.add_argument("--threads", action="store_true", help="break the cost down per thread")
    args = parser.parse_args()

    if not PROC.is_dir():
        print("error: /proc is not available; this only runs on Linux", file=sys.stderr)
        return 1

    pid = args.pid
    if pid is None:
        candidates = find_pids(args.match)
        if not candidates:
            print(f"error: no process matching {args.match!r}", file=sys.stderr)
            return 1
        if len(candidates) > 1:
            print(f"error: {len(candidates)} matches: {candidates}", file=sys.stderr)
            return 1
        pid = candidates[0]

    hz, cores = clock_ticks_per_second(), cpu_count()
    try:
        cmdline = (PROC / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        print(f"pid      : {pid}  ({cmdline.strip()})")
        print(f"machine  : {cores} online CPUs, {hz} ticks/s")

        if args.threads:
            rows = measure_threads(pid, args.seconds, hz, cores)
            total = sum(row[2].ticks for row in rows)
            print(f"window   : {args.seconds:.1f}s\n")
            print(f"{'thread':<16}{'tid':>8}{'ticks':>8}{'% core':>9}{'% machine':>11}")
            for name, tid, usage in rows:
                print(
                    f"{name:<16}{tid:>8}{usage.ticks:>8}"
                    f"{usage.of_one_core:>8.2f}%{usage.of_machine:>10.2f}%"
                )
            print(f"{'total':<16}{'':>8}{total:>8}")
        else:
            usage = measure(pid, args.seconds, hz, cores)
            print(f"window   : {usage.seconds:.2f}s")
            print(f"cpu time : {usage.ticks} ticks = {usage.ticks / hz:.3f}s")
            print(f"cpu      : {usage.of_one_core:.2f}% of one core")
            print(f"           {usage.of_machine:.2f}% of the machine")

        print(f"rss      : {read_rss_kb(pid)} kB")
    except FileNotFoundError:
        print(f"error: process {pid} is gone", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

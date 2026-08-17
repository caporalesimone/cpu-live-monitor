"""Linux implementation of the platform API.

Topology  : /sys/devices/system/cpu/...
Sampling  : /proc/stat
Memory    : /proc/meminfo
Input     : termios raw mode plus select, in :class:`PosixTerminal` below

The terminal half is POSIX rather than strictly Linux, and would serve a BSD or
macOS backend unchanged, but it lives here while Linux is its only caller: one
file per platform is easier to follow than a shared module with one user.

Every path is injectable, so the parsing can be exercised against a fake sysfs
on any platform — see tests/test_linux_backend.py. termios and tty are imported
lazily for the same reason: this module must be importable where they are not.
"""

from __future__ import annotations

import os
import select
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar, Final

from cpumon.backend.base import CpuSampler, PlatformBackend, TerminalBackend
from cpumon.core.errors import PlatformError
from cpumon.core.model import CoreClass, MemoryInfo, Topology
from cpumon.core.topology import build_topology

SYS_CPU: Final = Path("/sys/devices/system/cpu")
PROC: Final = Path("/proc")
# Where a board states its own name when /proc/cpuinfo does not, as on ARM.
_DEVICETREE_MODEL: Final = Path("/sys/firmware/devicetree/base/model")

# Logical CPUs that share one physical core. The first name is the modern one
# (Linux 5.3+); the second is its long-standing predecessor. Either answers the
# only question that matters here, and answers it for every architecture.
_SIBLING_FILES: Final = ("core_cpus_list", "thread_siblings_list")


def _read_text(path: Path) -> str | None:
    """File contents, stripped, or None when the file cannot be read."""
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    raw = _read_text(path)
    if raw is None or not raw.lstrip("-").isdigit():
        return None
    return int(raw)


def _parse_cpu_list(spec: str) -> list[int]:
    """Parses a sysfs cpulist such as '0-3,8,10-11'."""
    out: list[int] = []
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


class LinuxSampler(CpuSampler):
    """Per-CPU jiffy deltas from /proc/stat."""

    # user nice system idle iowait irq softirq steal guest guest_nice.
    # Only the first eight count towards the total; guest time is already
    # included in user and nice.
    _IDLE_FIELDS: ClassVar[tuple[int, int]] = (3, 4)  # idle, iowait
    _TOTAL_FIELDS: ClassVar[int] = 8

    def __init__(self, stat_path: Path | None = None) -> None:
        self._stat = stat_path if stat_path is not None else PROC / "stat"
        rows = self._read()
        self._n = len(rows)
        if self._n == 0:
            raise PlatformError(f"{self._stat} exposes no per-cpu rows")
        self._prev_idle = [0] * self._n
        self._prev_total = [0] * self._n
        self.sample()

    def _read(self) -> list[list[int]]:
        rows: list[list[int]] = []
        with self._stat.open("r", encoding="ascii") as fh:
            for line in fh:
                if not line.startswith("cpu"):
                    break
                name, _, rest = line.partition(" ")
                if name == "cpu":  # aggregate row
                    continue
                rows.append([int(v) for v in rest.split()])
        return rows

    def count(self) -> int:
        return self._n

    def sample(self) -> list[float]:
        rows = self._read()
        out: list[float] = []
        for i in range(self._n):
            fields = rows[i] if i < len(rows) else []
            idle = sum(fields[f] for f in self._IDLE_FIELDS if len(fields) > f)
            total = sum(fields[: self._TOTAL_FIELDS])
            d_idle = idle - self._prev_idle[i]
            d_total = total - self._prev_total[i]
            self._prev_idle[i] = idle
            self._prev_total[i] = total
            pct = 100.0 * (d_total - d_idle) / d_total if d_total > 0 else 0.0
            out.append(min(100.0, max(0.0, pct)))
        return out


class PosixTerminal(TerminalBackend):
    """cbreak-mode stdin, decoding the escape sequences the app cares about."""

    _SEQUENCES: ClassVar[dict[str, str]] = {
        "OP": "F1", "OQ": "F2", "OR": "F3", "OS": "F4",
        "[11~": "F1", "[12~": "F2", "[13~": "F3", "[14~": "F4",
        "[[A": "F1", "[[B": "F2", "[[C": "F3", "[[D": "F4",
        "[A": "UP", "[B": "DOWN", "OA": "UP", "OB": "DOWN",
        "[5~": "PGUP", "[6~": "PGDN",
        "[H": "HOME", "[F": "END", "OH": "HOME", "OF": "END",
        "[1~": "HOME", "[4~": "END",
    }  # fmt: skip

    # An escape byte is either a bare ESC or the start of a sequence. This grace
    # period is how the two are told apart, and the cap stops a long unknown
    # sequence from being read one key press at a time.
    _SEQUENCE_GRACE: ClassVar[float] = 0.03
    _SEQUENCE_MAX_LEN: ClassVar[int] = 6

    def __init__(self) -> None:
        self._saved: list[Any] | None = None
        self._fd: int | None = None

    def setup(self) -> None:
        # Imported here, not at module level: the rest of this module must stay
        # importable on platforms that have no termios, so its parsing can be
        # tested anywhere.
        import termios
        import tty

        if not sys.stdin.isatty():
            return
        self._fd = sys.stdin.fileno()
        # The ignores are for type checkers running on Windows, where the stdlib
        # stubs hide these POSIX-only symbols.
        self._saved = termios.tcgetattr(self._fd)  # type: ignore[attr-defined]
        tty.setcbreak(self._fd)  # type: ignore[attr-defined]

    def teardown(self) -> None:
        import termios

        if self._saved is not None and self._fd is not None:
            termios.tcsetattr(  # type: ignore[attr-defined]
                self._fd,
                termios.TCSADRAIN,  # type: ignore[attr-defined]
                self._saved,
            )

    @staticmethod
    def _wait(fd: int, timeout: float) -> bool:
        ready, _, _ = select.select([fd], [], [], timeout)
        return bool(ready)

    def read_key(self, timeout: float) -> str | None:
        fd = self._fd
        if fd is None:  # not a tty: nothing will ever arrive
            time.sleep(timeout)
            return None
        if not self._wait(fd, timeout):
            return None

        ch = os.read(fd, 1).decode("utf-8", "replace")
        if ch == "\x03":
            return "CTRL_C"
        if ch != "\x1b":
            return ch

        buf = ""
        while self._wait(fd, self._SEQUENCE_GRACE) and len(buf) < self._SEQUENCE_MAX_LEN:
            buf += os.read(fd, 1).decode("utf-8", "replace")
            if buf in self._SEQUENCES:
                return self._SEQUENCES[buf]
        return "ESC" if not buf else None


class LinuxBackend(PlatformBackend):
    """Linux platform backend."""

    name = "linux"

    _MEMINFO_KEYS: ClassVar[tuple[str, ...]] = (
        "MemTotal",
        "MemAvailable",
        "SwapTotal",
        "SwapFree",
    )

    def __init__(self, sys_cpu: Path | None = None, proc: Path | None = None) -> None:
        self._sys_cpu = sys_cpu if sys_cpu is not None else SYS_CPU
        self._proc = proc if proc is not None else PROC

    # -- topology ------------------------------------------------------------

    def read_topology(self) -> Topology:
        online = _read_text(self._sys_cpu / "online") or "0"
        cpu_ids = _parse_cpu_list(online)
        if not cpu_ids:
            raise PlatformError("no online CPUs reported by sysfs")

        klass_of = self._class_map(cpu_ids)
        cores = self._cores(cpu_ids)
        raw_cores = [(klass_of.get(min(lps), CoreClass.P), lps) for lps in cores]
        return build_topology(self._model_name(), raw_cores)

    def _cores(self, cpu_ids: Sequence[int]) -> list[list[int]]:
        """Group logical CPUs into physical cores, best source first."""
        return (
            self._by_sibling_list(cpu_ids)
            or self._by_package_and_core(cpu_ids)
            or [[cpu] for cpu in cpu_ids]
        )

    def _by_sibling_list(self, cpu_ids: Sequence[int]) -> list[list[int]] | None:
        """The authoritative grouping: who shares a core, straight from sysfs.

        Returns None unless every online CPU reports a sibling list. A partial
        answer would mix two notions of "core" in one topology.
        """
        online = set(cpu_ids)
        groups: list[list[int]] = []
        placed: set[int] = set()
        for cpu in cpu_ids:
            if cpu in placed:
                continue
            spec = self._first_readable(cpu, _SIBLING_FILES)
            if spec is None:
                return None
            members = sorted({cpu} | {c for c in _parse_cpu_list(spec) if c in online})
            placed.update(members)
            groups.append(members)
        return groups

    def _by_package_and_core(self, cpu_ids: Sequence[int]) -> list[list[int]] | None:
        """Fallback for kernels without the sibling lists.

        Both ids are required. A core_id is only guaranteed unique *within* a
        package, so without a package id the same core_id can legitimately repeat
        across clusters — pretending otherwise is how a 4-core ARM part gets
        reported as two cores with two threads each. Returning None instead falls
        through to one core per CPU, which is wrong in a way that misleads nobody.
        """
        if self._smt_active() is False:
            # The kernel states that no core has a second thread, so any grouping
            # this method produced would be an invention.
            return None

        groups: dict[tuple[int, int], list[int]] = {}
        for cpu in cpu_ids:
            base = self._sys_cpu / f"cpu{cpu}" / "topology"
            core = _read_int(base / "core_id")
            package = _read_int(base / "physical_package_id")
            if core is None or package is None:
                return None
            groups.setdefault((package, core), []).append(cpu)
        return [sorted(members) for _key, members in sorted(groups.items())]

    def _smt_active(self) -> bool | None:
        """Whether simultaneous multithreading is on, or None if unstated.

        Absent on kernels built without SMT support at all, which is the norm on
        ARM. Only consulted as a veto on the guessed grouping.
        """
        raw = _read_text(self._sys_cpu / "smt" / "active")
        return raw == "1" if raw in ("0", "1") else None

    def _first_readable(self, cpu: int, names: Sequence[str]) -> str | None:
        base = self._sys_cpu / f"cpu{cpu}" / "topology"
        for name in names:
            text = _read_text(base / name)
            if text:
                return text
        return None

    def _class_map(self, cpu_ids: Sequence[int]) -> dict[int, CoreClass]:
        """Best-effort hybrid detection.

        Preferred source is /sys/devices/system/cpu/types/, exposed by recent
        kernels on Intel hybrid parts. Falls back to cpu_capacity (ARM
        big.LITTLE), then to a uniform P assignment.
        """
        mapping: dict[int, CoreClass] = {}
        types_dir = self._sys_cpu / "types"
        if types_dir.is_dir():
            for entry in sorted(p.name for p in types_dir.iterdir()):
                spec = _read_text(types_dir / entry / "cpulist")
                if not spec:
                    continue
                if "atom" in entry:
                    klass = CoreClass.E
                elif "core" in entry:
                    klass = CoreClass.P
                else:
                    klass = CoreClass.UNKNOWN
                for cpu in _parse_cpu_list(spec):
                    mapping[cpu] = klass
            if mapping:
                return mapping

        caps: dict[int, int] = {}
        for cpu in cpu_ids:
            raw = _read_text(self._sys_cpu / f"cpu{cpu}" / "cpu_capacity")
            if raw and raw.isdigit():
                caps[cpu] = int(raw)
        if caps and len(set(caps.values())) > 1:
            top = max(caps.values())
            return {cpu: (CoreClass.P if cap == top else CoreClass.E) for cpu, cap in caps.items()}

        return dict.fromkeys(cpu_ids, CoreClass.P)

    def _model_name(self) -> str:
        """The CPU's name, from whichever source this machine happens to have.

        x86 reports "model name" in /proc/cpuinfo. ARM does not, so the device
        tree is tried next — on a board that is the most specific name available —
        before falling back to the architecture.
        """
        try:
            with (self._proc / "cpuinfo").open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass

        for path in (
            self._proc / "device-tree" / "model",
            _DEVICETREE_MODEL,
        ):
            # Device-tree strings are NUL terminated.
            model = (_read_text(path) or "").rstrip("\x00").strip()
            if model:
                return model

        return os.uname().machine if hasattr(os, "uname") else "Unknown CPU"

    # -- the rest of the platform API ----------------------------------------

    def create_sampler(self) -> CpuSampler:
        return LinuxSampler(self._proc / "stat")

    def create_terminal(self) -> TerminalBackend:
        return PosixTerminal()

    def uptime_seconds(self) -> float:
        raw = _read_text(self._proc / "uptime")
        if raw:
            try:
                return float(raw.split()[0])
            except (ValueError, IndexError):
                pass
        return 0.0

    def read_memory(self) -> MemoryInfo:
        values: dict[str, int] = {}
        meminfo = self._proc / "meminfo"
        try:
            with meminfo.open("r", encoding="ascii") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    if key in self._MEMINFO_KEYS:
                        values[key] = int(rest.split()[0]) * 1024
                        if len(values) == len(self._MEMINFO_KEYS):
                            break
        except (OSError, ValueError, IndexError) as exc:
            raise PlatformError(f"cannot read {meminfo}: {exc}") from exc

        swap_total = values.get("SwapTotal", 0)
        return MemoryInfo(
            total=values.get("MemTotal", 0),
            available=values.get("MemAvailable", 0),
            backing_kind="swap" if swap_total else "",
            backing_total=swap_total,
            backing_used=max(0, swap_total - values.get("SwapFree", 0)),
        )

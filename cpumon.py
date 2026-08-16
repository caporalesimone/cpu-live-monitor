#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CPU LIVE MONITOR
================

A terminal CPU monitor with per-logical-processor history, hybrid (P/E core)
awareness and a fully responsive layout.

The file is intentionally organised as a stack of independent layers, each
introduced by a banner comment. Every layer can be lifted into its own module
without touching the others:

    01  GLYPHS        unicode building blocks
    02  THEME         every colour in the program, in one place
    03  MODEL         platform-neutral data types
    04  PLATFORM API  abstract backend interfaces
    05  WINDOWS       Windows implementation of the platform API
    06  LINUX         Linux implementation of the platform API
    07  FACTORY       backend selection
    08  HISTORY       ring buffers for time series
    09  LAYOUT        responsive geometry solver
    10  WIDGETS       gauge / sparkline renderers (lookup-table driven)
    11  RENDERER      frame composition
    12  WORKERS       sampling and input threads
    13  APP           orchestration
    14  CLI           entry point

Platform coverage is deliberately asymmetric where the platforms themselves
differ: the SWAP row appears on Linux only, because Windows has no equivalent
figure that is worth a screen row. See MemoryInfo for the reasoning.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import struct
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple,
)

APP_NAME = "CPU LIVE MONITOR"
APP_NAME_SHORT = "CPUMON"
APP_VERSION = "1.19"
APP_AUTHOR = "Caporale Simone"
APP_YEAR = "2026"


# =============================================================================
# 01  GLYPHS
# =============================================================================


class Glyph:
    """Unicode building blocks used by the renderer."""

    # Horizontal bar: full cell plus the seven left-anchored partial cells.
    # U+2588 is the full block; U+2590 - n gives n/8 of a cell, n = 1..7.
    FULL = "\u2588"
    PARTIAL: Tuple[str, ...] = tuple(chr(0x2590 - n) for n in range(1, 8))

    # Vertical bar: eight levels, U+2581 (1/8) .. U+2588 (8/8).
    SPARK: Tuple[str, ...] = tuple(chr(0x2580 + n) for n in range(1, 9))

    H = "\u2500"      # light horizontal
    V = "\u2502"      # light vertical
    CROSS = "\u253c"     # light cross, rule meeting a column separator
    TEE_DOWN = "\u252c"  # light down-and-horizontal, top edge of the table
    TEE_UP = "\u2534"    # light up-and-horizontal, bottom edge of the table
    ARROW = "\u25ba"       # black right-pointing pointer
    ARROW_LEFT = "\u25c4"  # black left-pointing pointer
    SEAM = "\u250a"   # light quadruple dash vertical, marks a time-base break
    DOT = "\u00b7"    # middle dot
    COPY = "\u00a9"   # copyright sign


# =============================================================================
# 02  THEME
#
# Every colour in the program is defined here and nowhere else. Changing the
# look of the tool means editing this class only.
# =============================================================================


def _fg(n: int) -> str:
    """256-colour foreground escape."""
    return f"\x1b[38;5;{n}m"


def _bg(n: int) -> str:
    """256-colour background escape."""
    return f"\x1b[48;5;{n}m"


class Theme:
    RESET = "\x1b[0m"
    BOLD = "\x1b[1m"

    # --- chrome -------------------------------------------------------------
    TITLE = BOLD + _fg(255)
    VERSION = _fg(244)
    SUBTITLE = _fg(245)
    CLOCK = _fg(252)
    RULE = _fg(238)          # all table separators, horizontal and vertical
    COLUMN_HEAD = _fg(246)
    AXIS = _fg(240)
    MARKER = _fg(250)   # interval-change label drawn over the trend

    # --- values -------------------------------------------------------------
    LABEL = _fg(252)
    TOTAL_LABEL = BOLD + _fg(255)
    USAGE = _fg(252)
    COUNT = _fg(243)

    # --- core classes -------------------------------------------------------
    # Keys are the type tags produced by the model layer.
    CLASS: Dict[str, str] = {
        "P": _fg(75),
        "PHT": _fg(67),
        "E": _fg(114),
        "EHT": _fg(107),
        "LPE": _fg(80),
        "LPEH": _fg(66),
        "?": _fg(244),
    }
    CLASS_DEFAULT = _fg(244)

    # --- load gradient ------------------------------------------------------
    # (upper bound inclusive, colour). Last entry catches everything above.
    # Load gradients. Each entry is (inclusive upper bound, colour); the
    # bands are therefore 0..a, a+1..b, b+1..100. CPU and memory have
    # different tolerances, so they get independent scales.
    OK = _fg(78)     # calm green
    WARN_ = _fg(179)  # amber
    HOT = _fg(203)   # red

    CPU_LOAD_STEPS: Sequence[Tuple[int, str]] = (
        (39, OK),     # 0-39   green
        (74, WARN_),  # 40-74  amber
        (100, HOT),   # 75-100 red
    )
    MEM_LOAD_STEPS: Sequence[Tuple[int, str]] = (
        (49, OK),     # 0-49   green
        (74, WARN_),  # 50-74  amber
        (100, HOT),   # 75-100 red
    )

    # --- footer / help ------------------------------------------------------
    KEY_NUM = _fg(252)
    KEY_LABEL = _fg(235) + _bg(109)
    FOOTER_INFO = _fg(244)
    HELP_TITLE = BOLD + _fg(255)
    HELP_BODY = _fg(250)
    WARNING = _fg(203)

    @classmethod
    def class_colour(cls, tag: str) -> str:
        return cls.CLASS.get(tag, cls.CLASS_DEFAULT)

    # NOTE: colour lookup lives in LoadPalette (widgets layer), which caches
    # one entry per integer percent so the render loop never branches.


# =============================================================================
# 03  MODEL
#
# Platform-neutral description of the CPU. Backends produce these types; every
# layer above consumes only these types and never touches OS specifics.
# =============================================================================


class CoreClass(str, Enum):
    """Performance class of a physical core, ordered best to worst."""

    P = "P"      # performance core
    E = "E"      # efficiency core
    LPE = "LPE"  # low-power efficiency core (SoC tile)
    UNKNOWN = "?"


@dataclass(frozen=True)
class LogicalCpu:
    """One logical processor (one hardware thread)."""

    index: int          # index into the sampler's value array
    lp_id: int          # OS logical processor id
    core_id: int        # index of the owning physical core
    core_class: CoreClass
    smt_index: int      # 0 for the primary thread of the core

    @property
    def type_tag(self) -> str:
        """Short label, at most 4 characters, shown in the TYPE column."""
        base = self.core_class.value
        if self.smt_index == 0:
            return base
        return (base + "HT")[:4]


@dataclass(frozen=True)
class PhysicalCore:
    core_id: int
    core_class: CoreClass
    cpus: Tuple[LogicalCpu, ...]

    @property
    def label(self) -> str:
        """Compact id list, e.g. '0/1' for an SMT pair."""
        return "/".join(str(c.lp_id) for c in self.cpus)


@dataclass(frozen=True)
class MemoryInfo:
    """Memory snapshot in bytes, physical plus one backing-store metric.

    `backing_kind` names the secondary metric, or is empty when there is
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

    BACKING_ROW_LABEL = "SWAP"

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
    model_name: str
    cpus: Tuple[LogicalCpu, ...]
    cores: Tuple[PhysicalCore, ...]

    @property
    def n_cpus(self) -> int:
        return len(self.cpus)

    @property
    def n_cores(self) -> int:
        return len(self.cores)

    @property
    def classes(self) -> Tuple[CoreClass, ...]:
        """Distinct core classes, ordered best first."""
        order = [CoreClass.P, CoreClass.E, CoreClass.LPE, CoreClass.UNKNOWN]
        present = {c.core_class for c in self.cores}
        return tuple(k for k in order if k in present)

    @property
    def hybrid(self) -> bool:
        return len(self.classes) > 1

    def cpus_of_class(self, klass: CoreClass) -> Tuple[LogicalCpu, ...]:
        return tuple(c for c in self.cpus if c.core_class is klass)


# =============================================================================
# 04  PLATFORM API
#
# Everything OS specific lives behind these three interfaces. Porting to a new
# platform means implementing them and registering the backend in the factory.
# =============================================================================


class CpuSampler(ABC):
    """Produces per-logical-processor busy percentages."""

    @abstractmethod
    def count(self) -> int:
        """Number of logical processors this sampler reports on."""

    @abstractmethod
    def sample(self) -> List[float]:
        """Busy percentage per logical processor since the previous call.

        The first call after construction primes the deltas and returns zeros.
        """

    def close(self) -> None:  # pragma: no cover - optional hook
        pass


class TerminalBackend(ABC):
    """Raw terminal access: size, output, and normalised key input."""

    @abstractmethod
    def setup(self) -> None: ...

    @abstractmethod
    def teardown(self) -> None: ...

    @abstractmethod
    def read_key(self, timeout: float) -> Optional[str]:
        """Block up to *timeout* seconds. Returns a normalised key name.

        Normalised names: 'F1'..'F12', 'ESC', 'CTRL_C', or the literal
        character for ordinary keys. Returns None on timeout.
        """

    def size(self) -> Tuple[int, int]:
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


class PlatformError(RuntimeError):
    pass


# =============================================================================
# 05  WINDOWS BACKEND
#
# Topology  : GetLogicalProcessorInformationEx(RelationProcessorCore)
# Sampling  : NtQuerySystemInformation(SystemProcessorPerformanceInformation)
# Input     : msvcrt (blocking read on a daemon thread)
# =============================================================================

if sys.platform == "win32":  # pragma: no cover - platform specific
    import ctypes
    import ctypes.wintypes as wintypes
    import msvcrt
    import winreg

    _RELATION_PROCESSOR_CORE = 0
    _ERROR_INSUFFICIENT_BUFFER = 122
    _SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION = 8
    _ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    _STD_OUTPUT_HANDLE = -11

    # SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX, PROCESSOR_RELATIONSHIP variant
    # (x64 layout):
    #   0  DWORD Relationship
    #   4  DWORD Size
    #   8  BYTE  Flags
    #   9  BYTE  EfficiencyClass
    #  10  BYTE  Reserved[20]
    #  30  WORD  GroupCount
    #  32  GROUP_AFFINITY GroupMask[]   (16 bytes each on x64)
    _CORE_HEADER = struct.Struct("<IIBB20xH")
    _GROUP_AFFINITY = struct.Struct("<QH")
    _GROUP_AFFINITY_STRIDE = 16

    class WindowsTopologyReader:
        """Parses the packed variable-length structure returned by the API."""

        def __init__(self) -> None:
            self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            fn = self._k32.GetLogicalProcessorInformationEx
            fn.argtypes = [
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            fn.restype = ctypes.c_bool

        def read(self) -> List[Tuple[int, List[int]]]:
            """Returns [(efficiency_class, [logical processor ids]), ...]."""
            fn = self._k32.GetLogicalProcessorInformationEx
            size = ctypes.c_ulong(0)
            fn(_RELATION_PROCESSOR_CORE, None, ctypes.byref(size))
            err = ctypes.get_last_error()
            if size.value == 0:
                raise PlatformError(
                    f"GetLogicalProcessorInformationEx sizing failed (err={err})"
                )
            if err != _ERROR_INSUFFICIENT_BUFFER:
                raise PlatformError(
                    f"GetLogicalProcessorInformationEx unexpected error {err}"
                )

            buf = ctypes.create_string_buffer(size.value)
            if not fn(_RELATION_PROCESSOR_CORE, buf, ctypes.byref(size)):
                raise PlatformError(
                    "GetLogicalProcessorInformationEx failed "
                    f"(err={ctypes.get_last_error()})"
                )

            raw = buf.raw
            end = size.value
            offset = 0
            cores: List[Tuple[int, List[int]]] = []

            while offset + _CORE_HEADER.size <= end:
                rel, length, _flags, eff, groups = _CORE_HEADER.unpack_from(
                    raw, offset
                )
                if length < 8 or offset + length > end:
                    break
                if rel == _RELATION_PROCESSOR_CORE:
                    lps: List[int] = []
                    for g in range(groups):
                        mask, group = _GROUP_AFFINITY.unpack_from(
                            raw, offset + 32 + g * _GROUP_AFFINITY_STRIDE
                        )
                        lps.extend(
                            group * 64 + bit
                            for bit in range(64)
                            if mask >> bit & 1
                        )
                    cores.append((eff, sorted(lps)))
                offset += length

            if not cores:
                raise PlatformError("no processor cores reported by the OS")
            return cores

    class WindowsSampler(CpuSampler):
        """Per-processor idle/kernel/user tick deltas from ntdll.

        Buffer sizing note: the kernel does NOT accept an arbitrarily large
        buffer for this information class. It requires a length that is an
        exact multiple of sizeof(SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION);
        anything else is rejected with STATUS_INFO_LENGTH_MISMATCH, even when
        the buffer is far bigger than needed. The size is therefore asked for
        rather than guessed, with a short list of known-plausible strides as a
        fallback if the probe yields nothing.
        """

        _MAX_CPUS = 64  # one processor group
        _STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
        # x64: five LARGE_INTEGER fields plus one ULONG, padded to 8 bytes.
        _STRIDE_CANDIDATES = (48, 40, 56, 64, 32)

        def __init__(self) -> None:
            self._nt = ctypes.WinDLL("ntdll")
            fn = self._nt.NtQuerySystemInformation
            fn.argtypes = [
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.POINTER(ctypes.c_ulong),
            ]
            fn.restype = ctypes.c_long

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetActiveProcessorCount.argtypes = [ctypes.c_ushort]
            k32.GetActiveProcessorCount.restype = ctypes.c_ulong
            self._expected = int(k32.GetActiveProcessorCount(0))
            if not 1 <= self._expected <= self._MAX_CPUS:
                raise PlatformError(
                    f"unsupported active processor count {self._expected}"
                )

            self._ret = ctypes.c_ulong(0)
            self._buf = ctypes.create_string_buffer(1)
            self._size = 0
            self._stride = 0
            self._negotiate_buffer()

            self._prev_idle = [0] * self._expected
            self._prev_busy = [0] * self._expected
            self.sample()  # prime the deltas

        # -- buffer negotiation ---------------------------------------------

        def _raw_query(self, buf, length: int) -> int:
            """Returns the raw NTSTATUS without raising."""
            return int(
                self._nt.NtQuerySystemInformation(
                    _SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION,
                    buf,
                    length,
                    ctypes.byref(self._ret),
                )
            ) & 0xFFFFFFFF

        def _probe_required_length(self) -> int:
            """Ask the kernel how many bytes it wants."""
            self._ret.value = 0
            self._raw_query(None, 0)
            return self._ret.value

        def _candidate_sizes(self) -> List[int]:
            sizes: List[int] = []
            probed = self._probe_required_length()
            if probed:
                sizes.append(probed)
            sizes.extend(
                stride * self._expected for stride in self._STRIDE_CANDIDATES
            )
            seen = set()
            return [s for s in sizes if s > 0 and not (s in seen or seen.add(s))]

        def _negotiate_buffer(self) -> None:
            attempts: List[str] = []
            for size in self._candidate_sizes():
                buf = ctypes.create_string_buffer(size)
                status = self._raw_query(buf, size)
                if status != 0:
                    attempts.append(f"{size}B -> 0x{status:08X}")
                    continue
                returned = self._ret.value
                if returned == 0 or returned % self._expected:
                    attempts.append(f"{size}B -> returned {returned}, not a multiple")
                    continue
                stride = returned // self._expected
                if stride < 24:
                    attempts.append(f"{size}B -> implausible stride {stride}")
                    continue
                self._buf, self._size, self._stride = buf, size, stride
                return
            raise PlatformError(
                "could not negotiate a buffer for "
                "NtQuerySystemInformation(SystemProcessorPerformanceInformation) "
                f"with {self._expected} processors; tried: " + "; ".join(attempts)
            )

        # -- sampling ---------------------------------------------------------

        def _query(self) -> int:
            status = self._raw_query(self._buf, self._size)
            if status != 0:
                raise PlatformError(
                    f"NtQuerySystemInformation status=0x{status:08X} "
                    f"(buffer {self._size}B, stride {self._stride})"
                )
            return self._ret.value

        @property
        def stride(self) -> int:
            return self._stride

        @property
        def buffer_size(self) -> int:
            return self._size

        def count(self) -> int:
            return self._expected

        def sample(self) -> List[float]:
            self._query()
            raw = self._buf.raw
            out: List[float] = []
            for i in range(self._expected):
                base = i * self._stride
                # IdleTime, KernelTime, UserTime; KernelTime includes idle.
                idle, kernel, user = struct.unpack_from("<qqq", raw, base)
                total = kernel + user
                d_idle = idle - self._prev_idle[i]
                d_total = total - self._prev_busy[i]
                self._prev_idle[i] = idle
                self._prev_busy[i] = total
                if d_total > 0:
                    pct = 100.0 * (d_total - d_idle) / d_total
                else:
                    pct = 0.0
                out.append(min(100.0, max(0.0, pct)))
            return out

    class WindowsTerminal(TerminalBackend):
        # msvcrt returns a prefix byte then a scan code for function keys.
        # Function keys arrive behind the \x00 prefix, navigation keys behind
        # \xe0 (and behind \x00 too, from the numpad with NumLock off). The
        # scan codes do not overlap, so one table covers both prefixes.
        _SCAN_MAP = {
            ";": "F1", "<": "F2", "=": "F3", ">": "F4",
            "?": "F5", "@": "F6", "A": "F7", "B": "F8",
            "C": "F9", "D": "F10", "\x85": "F11", "\x86": "F12",
            "H": "UP", "P": "DOWN", "I": "PGUP", "Q": "PGDN",
            "G": "HOME", "O": "END",
        }

        def __init__(self) -> None:
            self._saved_mode: Optional[int] = None

        def setup(self) -> None:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = k32.GetStdHandle(_STD_OUTPUT_HANDLE)
            mode = ctypes.c_ulong()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                self._saved_mode = mode.value
                k32.SetConsoleMode(
                    handle, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING
                )
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass

        def teardown(self) -> None:
            if self._saved_mode is None:
                return
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = k32.GetStdHandle(_STD_OUTPUT_HANDLE)
            k32.SetConsoleMode(handle, self._saved_mode)

        def read_key(self, timeout: float) -> Optional[str]:
            deadline = time.monotonic() + timeout
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        code = msvcrt.getwch()
                        return self._SCAN_MAP.get(code)
                    if ch == "\x1b":
                        return "ESC"
                    if ch == "\x03":
                        return "CTRL_C"
                    return ch
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                time.sleep(min(0.02, remaining))

    class WindowsBackend(PlatformBackend):
        name = "windows"

        def __init__(self) -> None:
            if ctypes.sizeof(ctypes.c_void_p) != 8:
                raise PlatformError(
                    "a 64-bit Python interpreter is required: the native "
                    "structure layouts differ under WOW64"
                )

        def read_topology(self) -> Topology:
            raw_cores = WindowsTopologyReader().read()

            # EfficiencyClass: higher value means higher relative performance.
            # Two classes  -> P / E.  Three or more -> P / E / LPE.
            eff_values = sorted({eff for eff, _ in raw_cores})
            if len(eff_values) == 1:
                mapping = {eff_values[0]: CoreClass.P}
            elif len(eff_values) == 2:
                mapping = {eff_values[1]: CoreClass.P, eff_values[0]: CoreClass.E}
            else:
                mapping = {eff_values[-1]: CoreClass.P, eff_values[0]: CoreClass.LPE}
                for mid in eff_values[1:-1]:
                    mapping[mid] = CoreClass.E

            return _build_topology(
                model_name=self._model_name(),
                raw_cores=[(mapping[eff], lps) for eff, lps in raw_cores],
            )

        @staticmethod
        def _model_name() -> str:
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                )
                with key:
                    value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                    return str(value).strip()
            except OSError:
                return "Unknown CPU"

        def create_sampler(self) -> CpuSampler:
            return WindowsSampler()

        def create_terminal(self) -> TerminalBackend:
            return WindowsTerminal()

        def uptime_seconds(self) -> float:
            # GetTickCount64 excludes time spent in sleep/hibernate.
            k32 = ctypes.WinDLL("kernel32")
            k32.GetTickCount64.restype = ctypes.c_ulonglong
            return k32.GetTickCount64() / 1000.0

        def read_memory(self) -> MemoryInfo:
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if not k32.GlobalMemoryStatusEx(ctypes.byref(status)):
                raise PlatformError(
                    f"GlobalMemoryStatusEx failed (err={ctypes.get_last_error()})"
                )
            # No secondary metric is reported on Windows; see the note in
            # MemoryInfo for why the commit charge was measured and dropped.
            #
            # If a paging indicator is ever wanted here, the useful one is
            # hard faults per second, from NtQuerySystemInformation class 2
            # (SystemPerformanceInformation): its PageReadCount field is a
            # cumulative counter, so the same delta treatment used for the
            # CPU times applies. The structure is large and undocumented,
            # so expect to negotiate its size the way WindowsSampler does.
            return MemoryInfo(
                total=int(status.ullTotalPhys),
                available=int(status.ullAvailPhys),
            )


# =============================================================================
# 06  LINUX BACKEND
#
# Topology  : /sys/devices/system/cpu/...
# Sampling  : /proc/stat
# Input     : termios raw mode + select
#
# STATUS: written against the documented sysfs/procfs layout. Exercised on a
# non-hybrid x86-64 machine; hybrid class detection is untested on real
# Intel hybrid hardware.
# =============================================================================

_SYS_CPU = "/sys/devices/system/cpu"


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _parse_cpu_list(spec: str) -> List[int]:
    """Parses a sysfs cpulist such as '0-3,8,10-11'."""
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
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

    def __init__(self) -> None:
        rows = self._read()
        self._n = len(rows)
        if self._n == 0:
            raise PlatformError("/proc/stat exposes no per-cpu rows")
        self._prev_idle = [0] * self._n
        self._prev_total = [0] * self._n
        self.sample()

    @staticmethod
    def _read() -> List[List[int]]:
        rows: List[List[int]] = []
        with open("/proc/stat", "r", encoding="ascii") as fh:
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

    def sample(self) -> List[float]:
        rows = self._read()
        out: List[float] = []
        for i in range(self._n):
            fields = rows[i] if i < len(rows) else []
            # user nice system idle iowait irq softirq steal guest guest_nice
            idle = (fields[3] if len(fields) > 3 else 0) + (
                fields[4] if len(fields) > 4 else 0
            )
            total = sum(fields[:8])
            d_idle = idle - self._prev_idle[i]
            d_total = total - self._prev_total[i]
            self._prev_idle[i] = idle
            self._prev_total[i] = total
            pct = 100.0 * (d_total - d_idle) / d_total if d_total > 0 else 0.0
            out.append(min(100.0, max(0.0, pct)))
        return out


class PosixTerminal(TerminalBackend):
    """Raw-mode stdin with escape sequence decoding."""

    _SEQUENCES = {
        "OP": "F1", "OQ": "F2", "OR": "F3", "OS": "F4",
        "[11~": "F1", "[12~": "F2", "[13~": "F3", "[14~": "F4",
        "[[A": "F1", "[[B": "F2", "[[C": "F3", "[[D": "F4",
        "[A": "UP", "[B": "DOWN", "OA": "UP", "OB": "DOWN",
        "[5~": "PGUP", "[6~": "PGDN",
        "[H": "HOME", "[F": "END", "OH": "HOME", "OF": "END",
        "[1~": "HOME", "[4~": "END",
    }

    def __init__(self) -> None:
        self._saved = None
        self._fd = None

    def setup(self) -> None:
        import termios
        import tty

        if not sys.stdin.isatty():
            return
        self._fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)

    def teardown(self) -> None:
        import termios

        if self._saved is not None and self._fd is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def _wait(self, timeout: float) -> bool:
        import select

        if self._fd is None:
            time.sleep(timeout)
            return False
        ready, _, _ = select.select([self._fd], [], [], timeout)
        return bool(ready)

    def read_key(self, timeout: float) -> Optional[str]:
        if not self._wait(timeout):
            return None
        ch = os.read(self._fd, 1).decode("utf-8", "replace")
        if ch == "\x03":
            return "CTRL_C"
        if ch != "\x1b":
            return ch

        # Escape: could be a bare ESC or the prefix of a sequence. A short
        # grace period distinguishes the two.
        buf = ""
        while self._wait(0.03) and len(buf) < 6:
            buf += os.read(self._fd, 1).decode("utf-8", "replace")
            if buf in self._SEQUENCES:
                return self._SEQUENCES[buf]
        return self._SEQUENCES.get(buf, "ESC" if not buf else None)


class LinuxBackend(PlatformBackend):
    name = "linux"

    def read_topology(self) -> Topology:
        online = _read_text(f"{_SYS_CPU}/online") or "0"
        cpu_ids = _parse_cpu_list(online)
        if not cpu_ids:
            raise PlatformError("no online CPUs reported by sysfs")

        klass_of = self._class_map(cpu_ids)

        # Group logical CPUs by (package, core) pair.
        groups: Dict[Tuple[int, int], List[int]] = {}
        for cpu in cpu_ids:
            base = f"{_SYS_CPU}/cpu{cpu}/topology"
            core = _read_text(f"{base}/core_id")
            pkg = _read_text(f"{base}/physical_package_id")
            key = (int(pkg) if pkg else 0, int(core) if core else cpu)
            groups.setdefault(key, []).append(cpu)

        raw_cores = [
            (klass_of.get(min(lps), CoreClass.P), sorted(lps))
            for _key, lps in sorted(groups.items())
        ]
        return _build_topology(self._model_name(), raw_cores)

    @staticmethod
    def _class_map(cpu_ids: Sequence[int]) -> Dict[int, CoreClass]:
        """Best-effort hybrid detection.

        Preferred source is /sys/devices/system/cpu/types/, exposed by recent
        kernels on Intel hybrid parts. Falls back to cpu_capacity (ARM
        big.LITTLE), then to a uniform P assignment.
        """
        mapping: Dict[int, CoreClass] = {}
        types_dir = f"{_SYS_CPU}/types"
        if os.path.isdir(types_dir):
            for entry in sorted(os.listdir(types_dir)):
                spec = _read_text(f"{types_dir}/{entry}/cpulist")
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

        caps: Dict[int, int] = {}
        for cpu in cpu_ids:
            raw = _read_text(f"{_SYS_CPU}/cpu{cpu}/cpu_capacity")
            if raw and raw.isdigit():
                caps[cpu] = int(raw)
        if caps and len(set(caps.values())) > 1:
            top = max(caps.values())
            return {
                cpu: (CoreClass.P if cap == top else CoreClass.E)
                for cpu, cap in caps.items()
            }

        return {cpu: CoreClass.P for cpu in cpu_ids}

    @staticmethod
    def _model_name() -> str:
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return os.uname().machine if hasattr(os, "uname") else "Unknown CPU"

    def create_sampler(self) -> CpuSampler:
        return LinuxSampler()

    def create_terminal(self) -> TerminalBackend:
        return PosixTerminal()

    def uptime_seconds(self) -> float:
        raw = _read_text("/proc/uptime")
        if raw:
            try:
                return float(raw.split()[0])
            except (ValueError, IndexError):
                pass
        return 0.0

    _MEMINFO_KEYS = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")

    def read_memory(self) -> MemoryInfo:
        values: Dict[str, int] = {}
        try:
            with open("/proc/meminfo", "r", encoding="ascii") as fh:
                for line in fh:
                    key, _, rest = line.partition(":")
                    if key in self._MEMINFO_KEYS:
                        values[key] = int(rest.split()[0]) * 1024
                        if len(values) == len(self._MEMINFO_KEYS):
                            break
        except (OSError, ValueError, IndexError) as exc:
            raise PlatformError(f"cannot read /proc/meminfo: {exc}") from exc

        swap_total = values.get("SwapTotal", 0)
        return MemoryInfo(
            total=values.get("MemTotal", 0),
            available=values.get("MemAvailable", 0),
            backing_kind="swap" if swap_total else "",
            backing_total=swap_total,
            backing_used=max(0, swap_total - values.get("SwapFree", 0)),
        )


# =============================================================================
# 07  FACTORY
# =============================================================================


def _build_topology(
    model_name: str, raw_cores: Sequence[Tuple[CoreClass, Sequence[int]]]
) -> Topology:
    """Shared assembly step: turns (class, lp ids) pairs into a Topology.

    Both backends funnel through here so the index/SMT bookkeeping exists in
    exactly one place.
    """
    ordered = sorted(raw_cores, key=lambda item: min(item[1]))
    cpus: List[LogicalCpu] = []
    cores: List[PhysicalCore] = []

    for core_id, (klass, lp_ids) in enumerate(ordered):
        members: List[LogicalCpu] = []
        for smt_index, lp in enumerate(sorted(lp_ids)):
            cpu = LogicalCpu(
                index=lp,  # provisional; rewritten below
                lp_id=lp,
                core_id=core_id,
                core_class=klass,
                smt_index=smt_index,
            )
            members.append(cpu)
        cores.append(PhysicalCore(core_id, klass, tuple(members)))
        cpus.extend(members)

    cpus.sort(key=lambda c: c.lp_id)

    # The sampler indexes its result array by position, so the model must map
    # onto a contiguous 0..n-1 range. Rebuild with explicit indices and let the
    # caller validate the assumption.
    remap = {cpu.lp_id: i for i, cpu in enumerate(cpus)}
    cpus = [
        LogicalCpu(
            index=remap[c.lp_id],
            lp_id=c.lp_id,
            core_id=c.core_id,
            core_class=c.core_class,
            smt_index=c.smt_index,
        )
        for c in cpus
    ]
    by_id = {c.lp_id: c for c in cpus}
    cores = [
        PhysicalCore(
            core.core_id,
            core.core_class,
            tuple(by_id[m.lp_id] for m in core.cpus),
        )
        for core in cores
    ]
    return Topology(model_name, tuple(cpus), tuple(cores))


def create_backend() -> PlatformBackend:
    if sys.platform == "win32":
        return WindowsBackend()  # noqa: F821 - defined under the platform guard
    if sys.platform.startswith("linux"):
        return LinuxBackend()
    raise PlatformError(f"unsupported platform: {sys.platform}")


# =============================================================================
# 08  HISTORY
#
# Each metric owns an independent TimeSeries. A series records the wall-clock
# instant of every sample alongside its value, so the displayed time span is
# *measured*, never inferred from a global interval. That is what makes the
# time base decoupled: two series may be fed at different cadences, or by
# different collectors, and each still reports its own honest duration.
#
# The groundwork for a fixed-duration window (for example "always show the
# last 10 minutes regardless of interval") is the `window_seconds` field: when
# set, `tail_for_width` resamples the series onto the requested number of
# cells instead of taking the last N raw samples. Nothing sets it yet.
# =============================================================================


class SeriesKey:
    """Namespaced string keys into the history store."""

    TOTAL = "cpu:total"
    MEMORY = "mem:used"
    BACKING = "mem:backing"

    @staticmethod
    def klass(k: CoreClass) -> str:
        return f"cpu:class:{k.value}"

    @staticmethod
    def core(core_id: int) -> str:
        return f"cpu:core:{core_id}"

    @staticmethod
    def cpu(index: int) -> str:
        return f"cpu:lp:{index}"

    @staticmethod
    def group(size: int, class_value: str, bucket: int) -> str:
        return f"cpu:grp{size}:{class_value}:{bucket}"


@dataclass
class Sample:
    value: float
    at: float  # time.monotonic() when the sample was taken


class TrendPlan(NamedTuple):
    """How one row of trend is laid out.

    cells   one entry per column: None for a sample, otherwise the
            (label_char, seam_char) of a marker or of left padding
    samples how many sample values the plan consumes
    pad     leading columns that hold nothing, because the history is still
            shorter than the window
    """

    cells: List[Optional[Tuple[str, str]]]
    samples: int
    pad: int


class TimeSeries:
    """A ring buffer of timestamped samples with its own time base.

    Not thread-safe on its own; HistoryStore provides the lock.
    """

    __slots__ = ("capacity", "window_seconds", "_values", "_stamps")

    def __init__(self, capacity: int, window_seconds: Optional[float] = None) -> None:
        self.capacity = capacity
        # None  -> one cell per sample (current behaviour)
        # float -> one cell per (window_seconds / cells), resampled
        self.window_seconds = window_seconds
        self._values: deque = deque(maxlen=capacity)
        self._stamps: deque = deque(maxlen=capacity)

    def __len__(self) -> int:
        return len(self._values)

    def append(self, value: float, at: float) -> None:
        self._values.append(value)
        self._stamps.append(at)

    def clear(self) -> None:
        self._values.clear()
        self._stamps.clear()

    @property
    def latest(self) -> float:
        return self._values[-1] if self._values else 0.0

    def tail_for_width(self, cells: int) -> List[float]:
        """The values to draw in *cells* horizontal positions, oldest first."""
        if cells <= 0 or not self._values:
            return []
        if self.window_seconds is None:
            return list(self._values)[-cells:]
        return self._resample(cells, self.window_seconds)

    def span_for_width(self, cells: int) -> float:
        """Wall-clock duration actually covered by `tail_for_width(cells)`.

        A cell is a bucket, not an instant: the sample stamped at t reports
        activity over the interval that *ends* at t. The span therefore runs
        from the start of the oldest bucket, one interval before its stamp —
        measuring stamp-to-stamp would under-report by exactly one cell.
        """
        if cells <= 0 or not self._stamps:
            return 0.0
        if self.window_seconds is not None:
            return self.window_seconds

        shown = min(cells, len(self._stamps))
        if len(self._stamps) > shown:
            # The sample preceding the oldest visible one is still buffered,
            # so the start of its bucket is known exactly.
            return self._stamps[-1] - self._stamps[-shown - 1]
        if shown < 2:
            return 0.0
        # Nothing older is retained: extend by the mean of the visible gaps.
        inner = self._stamps[-1] - self._stamps[0]
        return inner * shown / (shown - 1)

    def _resample(self, cells: int, window: float) -> List[float]:
        """Bucket the last *window* seconds into *cells* averages.

        Reserved for the fixed-duration mode; kept here so the renderer never
        needs to know which mode a series is in.
        """
        now = self._stamps[-1]
        start = now - window
        step = window / cells
        buckets: List[List[float]] = [[] for _ in range(cells)]
        for value, stamp in zip(self._values, self._stamps):
            if stamp < start:
                continue
            idx = min(cells - 1, int((stamp - start) / step))
            buckets[idx].append(value)
        out: List[float] = []
        carry = 0.0
        for bucket in buckets:
            if bucket:
                carry = sum(bucket) / len(bucket)
            out.append(carry)  # hold the last known value across empty buckets
        return out


class HistoryStore:
    """Thread-safe collection of named TimeSeries."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._series: Dict[str, TimeSeries] = {}
        # Every push advances _seq; a marker pins a label to the sample that
        # will be pushed next, which is the first one taken at the new
        # cadence. Positions are therefore expressed in samples, not seconds,
        # exactly like the sparkline cells they annotate.
        self._seq = 0
        self._count = 0          # samples actually held (capped by capacity)
        self._markers: deque = deque(maxlen=32)

    def ensure(self, key: str, window_seconds: Optional[float] = None) -> None:
        with self._lock:
            if key not in self._series:
                self._series[key] = TimeSeries(self._capacity, window_seconds)

    def ensure_many(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.ensure(key)

    def mark(self, label: str) -> None:
        """Pin *label* to the next sample, flagging a break in the time base."""
        with self._lock:
            # A burst of key presses would stack unreadable labels: drop any
            # marker close enough to collide with this one.
            keep = [
                (seq, text)
                for seq, text in self._markers
                if self._seq - seq > len(label)
            ]
            keep.append((self._seq, label))
            self._markers = deque(keep, maxlen=32)

    def cell_plan(self, width: int) -> "TrendPlan":
        """Lay out *width* trend cells, oldest first.

        A marker is not painted over the data: it takes cells of its own and
        pushes everything older one block to the left. No sample is ever
        hidden — the oldest ones simply fall off the left edge, exactly as
        they would with the passage of time.

        Each entry is either None (draw the next sample here) or a
        (label_char, seam_char) pair. The returned plan also carries how many
        samples it consumes and how many leading cells are empty, so callers
        can label the axis over the region that actually holds data.
        """
        with self._lock:
            if width <= 0:
                return TrendPlan([], 0, 0)
            marks = {seq: label for seq, label in self._markers}
            plan: List[Optional[Tuple[str, str]]] = []

            def push_marker(label: str) -> None:
                seam = " " * (len(label) // 2) + Glyph.SEAM
                seam = seam.ljust(len(label))
                for i in range(len(label) - 1, -1, -1):
                    if len(plan) >= width:
                        return
                    plan.append((label[i], seam[i]))

            # A marker pinned to the not-yet-taken sample shows immediately,
            # so pressing a key gives feedback without waiting a whole period.
            if self._seq in marks:
                push_marker(marks[self._seq])

            oldest = self._seq - self._count
            seq = self._seq - 1
            while len(plan) < width and seq >= oldest:
                plan.append(None)
                if seq in marks:
                    push_marker(marks[seq])
                seq -= 1

            plan.reverse()
            pad = max(0, width - len(plan))
            if pad:
                plan = [(" ", " ")] * pad + plan
            return TrendPlan(plan, sum(1 for e in plan if e is None), pad)

    def push(self, values: Dict[str, float], at: Optional[float] = None) -> None:
        stamp = time.monotonic() if at is None else at
        with self._lock:
            self._seq += 1
            self._count = min(self._count + 1, self._capacity)
            cutoff = self._seq - self._capacity
            while self._markers and self._markers[0][0] < cutoff:
                self._markers.popleft()
            for key, value in values.items():
                series = self._series.get(key)
                if series is None:
                    series = self._series[key] = TimeSeries(self._capacity)
                series.append(value, stamp)

    def tail(self, key: str, width: int) -> List[float]:
        with self._lock:
            series = self._series.get(key)
            return series.tail_for_width(width) if series else []

    def span(self, key: str, width: int) -> float:
        with self._lock:
            series = self._series.get(key)
            return series.span_for_width(width) if series else 0.0

    def latest(self, key: str) -> float:
        with self._lock:
            series = self._series.get(key)
            return series.latest if series else 0.0

    def depth(self, key: str) -> int:
        with self._lock:
            series = self._series.get(key)
            return len(series) if series else 0

    def clear(self) -> None:
        with self._lock:
            for series in self._series.values():
                series.clear()
            self._markers.clear()
            self._count = 0


# =============================================================================
# 09  LAYOUT
#
# Pure function of (cols, rows, topology) -> Geometry. No I/O, no state, so it
# is trivially testable.
# =============================================================================


class RowMode(Enum):
    PER_CPU = "per-cpu"        # one row per logical processor
    PER_GROUP = "per-group"    # cores folded into buckets of N (N=1 -> per core)
    PER_CLASS = "per-class"    # one row per performance class
    TOTAL_ONLY = "total-only"  # aggregates only
    TOO_SHORT = "too-short"


class ColMode(Enum):
    FULL = "full"              # cpu | type | usage | gauge | history
    NO_HISTORY = "no-history"  # cpu | type | usage | gauge
    NO_GAUGE = "no-gauge"      # cpu | type | usage
    NO_TYPE = "no-type"        # cpu | usage
    TOO_NARROW = "too-narrow"


# Column widths. The whole horizontal geometry derives from these.
W_CPU = 5
W_TYPE = 4
W_USAGE = 5   # "99.9%" or "100%" — see _fmt_percent
W_GAUGE = 10
SEP_W = 3           # " | "
MIN_HISTORY = 10
MAX_HISTORY = 400

_W_CPU_USAGE = 1 + W_CPU + SEP_W + W_USAGE                   # 15
_W_WITH_TYPE = 1 + W_CPU + SEP_W + W_TYPE + SEP_W + W_USAGE  # 22
_W_WITH_GAUGE = _W_WITH_TYPE + SEP_W + W_GAUGE               # 35
_W_WITH_HISTORY = _W_WITH_GAUGE + SEP_W                      # 38 + history

# Vertical chrome, counted row by row so the solver and the renderer cannot
# drift apart:
#   title, subtitle, rule, column head, head separator,
#   [aggregates], [separator + core rows], separator, RAM, rule, footer
_CHROME_WITH_TABLE = 10   # includes the separator that precedes the core rows
_CHROME_NO_TABLE = 9      # aggregates and RAM only, still under a column head
_CHROME_MINIMAL = 7       # title, subtitle, rule, TOTAL, RAM, rule, footer

# Bucket sizes tried, in order, when folding cores to save vertical space.
GROUP_SIZES: Tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12, 16)


@dataclass(frozen=True)
class Geometry:
    cols: int
    rows: int
    col_mode: ColMode
    row_mode: RowMode
    history_width: int
    line_width: int
    summary_rows: int
    body_rows: int
    group_size: int = 1
    show_backing: bool = False   # SWAP / COMMIT row under RAM

    @property
    def show_type(self) -> bool:
        return self.col_mode in (ColMode.FULL, ColMode.NO_HISTORY, ColMode.NO_GAUGE)

    @property
    def show_gauge(self) -> bool:
        return self.col_mode in (ColMode.FULL, ColMode.NO_HISTORY)

    @property
    def show_history(self) -> bool:
        return self.col_mode is ColMode.FULL

    @property
    def show_table(self) -> bool:
        return self.row_mode in (RowMode.PER_CPU, RowMode.PER_GROUP)

    @property
    def usable(self) -> bool:
        return (
            self.col_mode is not ColMode.TOO_NARROW
            and self.row_mode is not RowMode.TOO_SHORT
        )


class LayoutSolver:
    """Pure function of (cols, rows, topology) -> Geometry."""

    def __init__(self, topology: Topology, has_backing: bool = False) -> None:
        self._topo = topology
        self._n_classes = len(topology.classes) if topology.hybrid else 0
        # The SWAP/COMMIT row costs one row in every mode. Whether it exists
        # is a property of the machine, so it is fixed at construction and
        # both the solver and the renderer read it from the Geometry.
        self._has_backing = has_backing
        self._extra = 1 if has_backing else 0
        # Cores per class, used to size the folded views.
        self._class_sizes: Tuple[int, ...] = tuple(
            sum(1 for c in topology.cores if c.core_class is k)
            for k in topology.classes
        )

    # -- public -------------------------------------------------------------

    def solve(self, cols: int, rows: int) -> Geometry:
        col_mode, history_width, line_width = self._solve_columns(cols)
        row_mode, summary_rows, body_rows, group = self._solve_rows(rows)
        return Geometry(
            cols=cols,
            rows=rows,
            col_mode=col_mode,
            row_mode=row_mode,
            history_width=history_width,
            line_width=line_width,
            summary_rows=summary_rows,
            body_rows=body_rows,
            group_size=group,
            show_backing=self._has_backing,
        )

    def rows_for_group(self, group: int) -> int:
        """Body rows produced by folding each class into buckets of *group*."""
        return sum(-(-n // group) for n in self._class_sizes)

    # -- internals -----------------------------------------------------------

    def _solve_columns(self, cols: int) -> Tuple[ColMode, int, int]:
        if cols >= _W_WITH_HISTORY + MIN_HISTORY:
            width = min(MAX_HISTORY, cols - _W_WITH_HISTORY)
            return ColMode.FULL, width, _W_WITH_HISTORY + width
        if cols >= _W_WITH_GAUGE:
            return ColMode.NO_HISTORY, 0, _W_WITH_GAUGE
        if cols >= _W_WITH_TYPE:
            return ColMode.NO_GAUGE, 0, _W_WITH_TYPE
        if cols >= _W_CPU_USAGE:
            return ColMode.NO_TYPE, 0, _W_CPU_USAGE
        return ColMode.TOO_NARROW, 0, cols

    def _solve_rows(self, rows: int) -> Tuple[RowMode, int, int, int]:
        # Aggregate block: TOTAL plus one row per performance class. Memory is
        # not part of it — it sits on its own below the CPU rows.
        aggregates = 1 + self._n_classes
        overhead = _CHROME_WITH_TABLE + self._extra + aggregates

        if rows >= overhead + self._topo.n_cpus:
            return RowMode.PER_CPU, aggregates, self._topo.n_cpus, 1

        # Fold cores into progressively larger buckets before giving up on the
        # table. Each step still shows more detail than the per-class view.
        for group in GROUP_SIZES:
            body = self.rows_for_group(group)
            if body <= self._n_classes:
                break  # folding further just reproduces the per-class view
            if rows >= overhead + body:
                return RowMode.PER_GROUP, aggregates, body, group

        if rows >= _CHROME_NO_TABLE + self._extra + aggregates and self._n_classes:
            return RowMode.PER_CLASS, aggregates, 0, 1
        if rows >= _CHROME_MINIMAL + self._extra:
            return RowMode.TOTAL_ONLY, 1, 0, 1
        return RowMode.TOO_SHORT, 0, 0, 1


# =============================================================================
# 10  WIDGETS
#
# Both widgets are driven by lookup tables built once at import time. At render
# time there is no arithmetic per cell, only array indexing.
# =============================================================================


class LoadPalette:
    """Colour lookup for one metric, cached per integer percent.

    Bands come straight from a Theme step list, so adding or moving a
    threshold is a one-line change in Theme and nothing else.
    """

    __slots__ = ("steps", "colour")

    def __init__(self, steps: Sequence[Tuple[int, str]]) -> None:
        self.steps = tuple(steps)
        self.colour: List[str] = [self._pick(p) for p in range(101)]

    def _pick(self, percent: int) -> str:
        for upper, colour in self.steps:
            if percent <= upper:
                return colour
        return self.steps[-1][1]

    def bands(self) -> List[Tuple[int, int, str]]:
        """(low, high, colour) for each band, inclusive on both ends."""
        out: List[Tuple[int, int, str]] = []
        low = 0
        for upper, colour in self.steps:
            out.append((low, upper, colour))
            low = upper + 1
        return out


def _fmt_percent(value: float) -> str:
    """Percentage that never exceeds five characters.

    One decimal below full scale, none at full scale. The threshold is 99.95
    and not 100.0 because "{:.1f}".format(99.96) is "100.0%", which would be
    six characters and push the whole row one column out of alignment.
    """
    if value >= 99.95:
        return "100%"
    if value <= 0.0:
        return "0.0%"
    return f"{value:.1f}%"


def _clamp_percent(value: float) -> int:
    if value <= 0.0:
        return 0
    if value >= 100.0:
        return 100
    return int(value + 0.5)


class GaugeLut:
    """Horizontal bar, `cells` wide, with 1/8-cell resolution.

    The empty part is padded with spaces rather than a shading glyph: a
    partial block covers only a fraction of its cell and leaves the terminal
    background showing, so an adjacent shaded cell produces a visible seam.
    """

    def __init__(self, cells: int) -> None:
        self.cells = cells
        self.fill: List[str] = []
        self.pad: List[str] = []
        for percent in range(101):
            eighths = int(round(percent / 100.0 * cells * 8))
            full, rem = divmod(eighths, 8)
            if full >= cells:
                full, rem = cells, 0
            text = Glyph.FULL * full
            if rem:
                text += Glyph.PARTIAL[rem - 1]
            self.fill.append(text)
            self.pad.append(" " * (cells - len(text)))

    def render(self, value: float, palette: "LoadPalette") -> str:
        idx = _clamp_percent(value)
        return palette.colour[idx] + self.fill[idx] + Theme.RESET + self.pad[idx]


class SparkLut:
    """Vertical eight-level sparkline with a colour per sample."""

    def __init__(self) -> None:
        # Glyph height is metric-independent; only the colour differs.
        self.glyph = [
            Glyph.SPARK[min(7, max(0, -(-p * 8 // 100) - 1))] for p in range(101)
        ]

    def render(
        self,
        plan: Sequence[Optional[Tuple[str, str]]],
        samples: Sequence[float],
        palette: "LoadPalette",
        marker_colour: str = "",
        with_label: bool = False,
    ) -> str:
        """Draw the cells described by *plan*.

        Sample slots consume `samples` in order; marker slots draw their own
        character. `with_label` picks the spelled-out variant (one row) over
        the bare seam (every other row).
        """
        if not plan:
            return ""
        colours = palette.colour
        glyphs = self.glyph
        parts: List[str] = []
        current = ""
        index = 0

        for entry in plan:
            if entry is None:
                value = samples[index] if index < len(samples) else 0.0
                index += 1
                idx = _clamp_percent(value)
                colour, char = colours[idx], glyphs[idx]
            else:
                char = entry[0] if with_label else entry[1]
                colour = marker_colour if char != " " else ""
            if colour != current:
                parts.append(colour if colour else Theme.RESET)
                current = colour
            parts.append(char)

        parts.append(Theme.RESET)
        return "".join(parts)


GAUGE = GaugeLut(W_GAUGE)
SPARK = SparkLut()

PALETTE_CPU = LoadPalette(Theme.CPU_LOAD_STEPS)
PALETTE_MEM = LoadPalette(Theme.MEM_LOAD_STEPS)

# Tick label printed above the gauge column. Built rather than hard-coded so a
# change to W_GAUGE cannot silently desynchronise it from the bar below.
def _gauge_scale_label(cells: int) -> str:
    """Tick row above the gauge: ends labelled, midpoint marked with a dot."""
    label = [" "] * cells
    left, right, mid = "0%", "100%", Glyph.DOT
    if len(left) + len(right) + 1 > cells:
        return label and "".join(label)
    for i, ch in enumerate(left):
        label[i] = ch
    start = cells - len(right)
    for i, ch in enumerate(right):
        label[start + i] = ch
    centre = (cells - 1) // 2
    if label[centre] == " ":
        label[centre] = mid
    return "".join(label)


GAUGE_SCALE_LABEL = _gauge_scale_label(W_GAUGE)
assert len(GAUGE_SCALE_LABEL) == W_GAUGE


# =============================================================================
# 11  RENDERER
#
# Composes a complete frame into a single string. The caller performs exactly
# one write per frame.
# =============================================================================


CSI = "\x1b["


def _at(row: int) -> str:
    """Move to the start of *row* (0-based) and clear it."""
    return f"{CSI}{row + 1};1H{CSI}2K"


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _fmt_window(seconds: float) -> str:
    """Compact duration: seconds up to 59, then minutes and seconds.

    Rounding is applied before choosing the unit, otherwise 59.6 s would be
    formatted as "60s" instead of rolling over to "1m 00s".
    """
    if seconds < 9.95:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        minutes, secs = divmod(total, 60)
        return f"{minutes}m {secs:02d}s"
    hours, rest = divmod(total, 3600)
    return f"{hours}h {rest // 60:02d}m"


def core_buckets(
    topology: Topology, group_size: int
) -> List[Tuple[CoreClass, List[List[PhysicalCore]]]]:
    """Split each performance class into buckets of at most *group_size* cores.

    Shared by the renderer (labels) and the collector (series keys) so the two
    can never disagree about which cores belong to which row.
    """
    out: List[Tuple[CoreClass, List[List[PhysicalCore]]]] = []
    for klass in topology.classes:
        members = [c for c in topology.cores if c.core_class is klass]
        buckets = [
            members[i : i + group_size] for i in range(0, len(members), group_size)
        ]
        out.append((klass, buckets))
    return out


def bucket_label(cores: Sequence[PhysicalCore]) -> str:
    """Compact identifier for a row covering one or more physical cores."""
    if len(cores) == 1:
        return cores[0].label
    ids = [c.lp_id for core in cores for c in core.cpus]
    return f"{min(ids)}-{max(ids)}"


@dataclass
class RowSpec:
    """One rendered line of the table or summary block.

    Every row occupies exactly the same columns, so all trends share one time
    axis: the newest sample sits in the last cell of the history column, on
    every row without exception.
    """

    cpu_text: str
    type_text: str
    type_colour: str
    series_key: str
    emphasise: bool = False
    palette: LoadPalette = PALETTE_CPU
    marker_label: bool = False  # print the interval text, not just the seam


class Renderer:
    CLOCK_ROW = 0

    def __init__(self, topology: Topology, history: HistoryStore) -> None:
        self._topo = topology
        self._history = history
        self._clock_col = 0
        self._plan_cache: Optional[TrendPlan] = None
        self._plan_cache_key: int = -1

    # -- public API ---------------------------------------------------------

    def frame(self, geom: Geometry, state: "UiState") -> str:
        self.invalidate_plan()  # the trend advances between frames
        if not geom.usable:
            return self._too_small(geom)
        if state.help_visible:
            return self._help(geom, state)
        return self._dashboard(geom, state)

    def clock_only(self, geom: Geometry) -> str:
        """Cheap partial update: repaint the clock without touching the rest."""
        if self._clock_col <= 0:
            return ""
        text = self._clock_text()
        return (
            f"{CSI}{self.CLOCK_ROW + 1};{self._clock_col}H"
            + Theme.CLOCK
            + text
            + Theme.RESET
        )

    # -- screens ------------------------------------------------------------

    def _too_small(self, geom: Geometry) -> str:
        msg = "window too small" if geom.cols >= 16 else "too small"
        detail = f"{geom.cols}x{geom.rows}"
        need = f"min {_W_CPU_USAGE}x{_CHROME_MINIMAL}"
        out = [f"{CSI}2J"]
        row = max(0, geom.rows // 2 - 1)
        for offset, text in enumerate((msg, detail, need)):
            if row + offset >= geom.rows:
                break
            text = text[: geom.cols]
            pad = max(0, (geom.cols - len(text)) // 2)
            colour = Theme.WARNING if offset == 0 else Theme.SUBTITLE
            out.append(_at(row + offset) + " " * pad + colour + text + Theme.RESET)
        return "".join(out)

    # -- help screen ---------------------------------------------------------

    # Hardcoded sample values for the legend. 45 is deliberate: it is the one
    # point where the two scales disagree (amber for CPU, still green for
    # RAM), so the example rows visibly differ instead of looking identical.
    _LEGEND_SAMPLES: Tuple[int, ...] = (20, 45, 85)

    # Every tag LogicalCpu.type_tag can produce, with what it means. Listed in
    # full rather than filtered to the current machine, so the help doubles as
    # documentation of the classification itself.
    _TYPE_LEGEND: Tuple[Tuple[str, str], ...] = (
        ("P", "performance core, primary thread"),
        ("PHT", "performance core, SMT sibling"),
        ("E", "efficiency core"),
        ("EHT", "efficiency core, SMT sibling"),
        ("LPE", "low-power efficiency core, SoC tile"),
        ("LPEH", "low-power efficiency core, SMT sibling"),
        ("?", "class not reported by the operating system"),
    )

    @classmethod
    def _type_legend(cls) -> List[Tuple[str, str]]:
        rows = []
        for tag, meaning in cls._TYPE_LEGEND:
            plain = f"{tag.ljust(6)}{meaning}"
            styled = (
                Theme.class_colour(tag) + tag.ljust(6) + Theme.RESET
                + Theme.HELP_BODY + meaning + Theme.RESET
            )
            rows.append((plain, styled))
        return rows

    @staticmethod
    def _band_legend(label: str, palette: LoadPalette) -> Tuple[str, str]:
        """(plain, styled) line showing every colour band and its range."""
        plain = label.ljust(6)
        styled = Theme.HELP_BODY + plain + Theme.RESET
        for low, high, colour in palette.bands():
            swatch = Glyph.FULL * 4
            text = f" {low}-{high}%".ljust(10)
            plain += swatch + text
            styled += colour + swatch + Theme.RESET + Theme.HELP_BODY + text
        return plain, styled + Theme.RESET

    @classmethod
    def _gauge_legend(cls, label: str, palette: LoadPalette) -> Tuple[str, str]:
        """(plain, styled) line showing the gauge at one value per band."""
        plain = label.ljust(6)
        styled = Theme.HELP_BODY + plain + Theme.RESET
        for value in cls._LEGEND_SAMPLES:
            bar_plain = GAUGE.fill[value] + GAUGE.pad[value]
            tail = f" {value:>3}%   "
            plain += bar_plain + tail
            styled += GAUGE.render(value, palette) + Theme.HELP_BODY + tail
        return plain, styled + Theme.RESET

    @staticmethod
    def _spark_legend() -> Tuple[str, str]:
        ramp = [i * 100 / 15 for i in range(16)]
        plain = "trend " + "".join(
            SPARK.glyph[_clamp_percent(v)] for v in ramp
        ) + "   idle on the left, busiest on the right"
        # A plan of plain sample slots: the legend has no markers in it.
        plan: List[Optional[Tuple[str, str]]] = [None] * len(ramp)
        styled = (
            Theme.HELP_BODY + "trend " + Theme.RESET
            + SPARK.render(plan, ramp, PALETTE_CPU)
            + Theme.HELP_BODY + "   idle on the left, busiest on the right"
            + Theme.RESET
        )
        return plain, styled

    def _help_rows(self) -> List[Tuple[str, str]]:
        """The complete help text as (plain, styled) pairs, one per line."""
        def line(text: str, colour: str = Theme.HELP_BODY) -> Tuple[str, str]:
            return text, colour + text + Theme.RESET

        blank = ("", "")
        rows: List[Tuple[str, str]] = [
            line(f"{APP_NAME} v{APP_VERSION}", Theme.HELP_TITLE),
            line(f"{APP_AUTHOR} {Glyph.DOT} {Glyph.COPY} {APP_YEAR}", Theme.SUBTITLE),
            blank,
            line("keys", Theme.HELP_TITLE),
            line("F1    toggle this help"),
            line("      Up/Down and PgUp/PgDn scroll it, Esc closes"),
            line(f"F2    faster sampling  (-{INTERVAL_STEP:.1f}s)"),
            line(f"F3    slower sampling  (+{INTERVAL_STEP:.1f}s)"),
            line(f"      range {INTERVAL_MIN:.1f}s to {INTERVAL_MAX:.1f}s"),
            line("Esc   quit"),
            blank,
            line("columns", Theme.HELP_TITLE),
            line("CPU     logical processor id; an id pair or range when folded"),
            line("TYPE    core class, or memory capacity on the RAM row"),
            line("USAGE   busy share since the previous sample"),
            line("gauge   the same value, one cell per 10%"),
            line("History oldest sample left, newest right; the ruler above"),
            line("        spans the samples on screen and states their age"),
            line("        a gap with a figure marks an interval change;"),
            line("        cells left of it cover a different amount of time"),
            line("RAM     used share of physical memory; TYPE shows capacity"),
            line("SWAP    pages written out to swap devices. Linux only, and"),
            line("        hidden when no swap is configured. Windows shows no"),
            line("        such row: its commit charge tracks RAM too closely"),
            line("        to be worth a line, and pagefile use says little"),
            line("        about pressure because the system writes there"),
            line("        proactively even with memory to spare."),
            blank,
            line("core types", Theme.HELP_TITLE),
        ] + self._type_legend() + [
            blank,
            line("colour thresholds", Theme.HELP_TITLE),
            self._band_legend("CPU", PALETTE_CPU),
            self._band_legend("RAM", PALETTE_MEM),
            blank,
            line("gauge at a glance", Theme.HELP_TITLE),
            self._gauge_legend("CPU", PALETTE_CPU),
            self._gauge_legend("RAM", PALETTE_MEM),
            line("      note 45% is amber for CPU but still green for RAM",
                 Theme.SUBTITLE),
            self._spark_legend(),
            blank,
            line("layout", Theme.HELP_TITLE),
            line("Shrinking the window folds SMT siblings, then groups cores,"),
            line("then collapses to per-class rows, then to totals alone."),
            line("Narrowing drops history, then the gauge, then the type column."),
            blank,
        ]

        return rows

    def help_line_count(self) -> int:
        return len(self._help_rows())

    @staticmethod
    def help_viewport(geom: Geometry) -> int:
        """Text lines visible at once.

        Two rows are held back: the last for the key bar, and the one above it
        left blank so the text never touches it. On a window too short for
        both, the blank row is the first thing given up.
        """
        return max(1, geom.rows - 2)

    def help_max_scroll(self, geom: Geometry) -> int:
        return max(0, self.help_line_count() - self.help_viewport(geom))

    def _help(self, geom: Geometry, state: "UiState") -> str:
        rows = self._help_rows()
        viewport = self.help_viewport(geom)
        top = min(max(0, state.help_scroll), max(0, len(rows) - viewport))

        out = [f"{CSI}2J"]
        for i, (plain, styled) in enumerate(rows[top:top + viewport]):
            # Styled text is emitted whole or not at all: slicing it would cut
            # an escape sequence in half.
            body = styled if len(plain) + 1 <= geom.cols else plain[: geom.cols - 1]
            out.append(_at(i) + " " + body)
        out.append(
            _at(geom.rows - 1)
            + self._help_status(geom, top, viewport, len(rows))
        )
        return "".join(out)

    def _help_status(
        self, geom: Geometry, top: int, viewport: int, total: int
    ) -> str:
        def key(num: str, label: str) -> Tuple[str, int]:
            return (
                Theme.KEY_NUM + num + Theme.RESET
                + Theme.KEY_LABEL + label + Theme.RESET,
                len(num) + len(label),
            )

        scrollable = total > viewport
        hints = [("Esc", "Close ")]
        if scrollable:
            hints = [("Up/Dn", "Scroll "), ("PgUp/PgDn", "Page "), ("Esc", "Close ")]

        parts, used = [], 1
        for num, label in hints:
            styled, size = key(num, label)
            if used + size > geom.cols:
                break
            parts.append(styled)
            used += size
        left = " " + "".join(parts)

        if not scrollable:
            return left
        shown_to = min(top + viewport, total)
        right_text = f"{top + 1}-{shown_to} of {total}"
        if used + len(right_text) + 2 > geom.cols:
            return left
        gap = geom.cols - used - len(right_text) - 1
        return left + " " * gap + Theme.FOOTER_INFO + right_text + Theme.RESET

    def _dashboard(self, geom: Geometry, state: "UiState") -> str:
        width = geom.line_width
        out: List[str] = [f"{CSI}2J"]
        row = 0

        # --- title line with clock -----------------------------------------
        # The credit line degrades from the back: copyright, then author,
        # then the version, then the name itself shortens.
        version = f"v{APP_VERSION}"
        for name, trail in (
            (APP_NAME, f"{version} - {APP_AUTHOR} - {Glyph.COPY} {APP_YEAR}"),
            (APP_NAME, f"{version} - {APP_AUTHOR}"),
            (APP_NAME, version),
            (APP_NAME_SHORT, version),
            (APP_NAME_SHORT, ""),
        ):
            left_len = 1 + len(name) + (1 + len(trail) if trail else 0)
            if left_len + 2 <= width:
                break
        left = f" {Theme.TITLE}{name}{Theme.RESET}"
        if trail:
            left += f" {Theme.VERSION}{trail}{Theme.RESET}"
        clock = self._clock_text(max(0, width - left_len - 2))
        if clock:
            gap = width - left_len - len(clock)
            self._clock_col = left_len + gap + 1
            out.append(_at(row) + left + " " * gap + Theme.CLOCK + clock + Theme.RESET)
        else:
            self._clock_col = 0
            out.append(_at(row) + left)
        row += 1

        # --- subtitle -------------------------------------------------------
        out.append(_at(row) + self._subtitle(geom, state))
        row += 1

        out.append(_at(row) + self._column_rule(geom, Glyph.TEE_DOWN))
        row += 1

        # --- one table: aggregates, then cores, then memory -------------------
        #
        # The CPU aggregates belong to the same grid as the cores they
        # summarise, separated by a rule rather than living in a block of
        # their own. Memory is a different kind of quantity, so it sits below
        # the CPU rows behind its own rule instead of between them.
        minimal = geom.row_mode is RowMode.TOTAL_ONLY

        if not minimal:
            out.append(_at(row) + self._column_head(geom))
            row += 1
            out.append(_at(row) + self._column_rule(geom))
            row += 1

        for spec in self._aggregate_specs(geom):
            out.append(_at(row) + self._render_row(spec, geom))
            row += 1

        if geom.show_table:
            out.append(_at(row) + self._column_rule(geom))
            row += 1
            for spec in self._body_specs(geom):
                out.append(_at(row) + self._render_row(spec, geom))
                row += 1

        if not minimal:
            out.append(_at(row) + self._column_rule(geom))
            row += 1
        out.append(_at(row) + self._render_row(self._memory_spec(state), geom))
        row += 1
        backing = self._backing_spec(state) if geom.show_backing else None
        if backing is not None and row < geom.rows - 1:
            out.append(_at(row) + self._render_row(backing, geom))
            row += 1

        # --- close the table, then pin the key bar to the last row ------------
        # The rule sits immediately under the memory row so the table reads as
        # closed, while the key bar stays at the bottom edge: its position is
        # what tells the eye where the window actually ends.
        out.append(_at(row) + self._column_rule(geom, Glyph.TEE_UP))
        out.append(_at(geom.rows - 1) + self._footer(geom, state))
        return "".join(out)

    # -- pieces --------------------------------------------------------------

    @staticmethod
    def _clock_text(width_budget: int = 999) -> str:
        """Longest clock format that fits the available space."""
        for fmt in ("%a %d %b %Y  %H:%M:%S", "%d %b  %H:%M:%S", "%H:%M:%S"):
            text = time.strftime(fmt)
            if len(text) <= width_budget:
                return text
        return ""

    def _subtitle(self, geom: Geometry, state: "UiState") -> str:
        """Machine description on the left, sampling interval on the right.

        The interval sits under the clock rather than in the footer: both are
        "what the numbers on screen refer to", and keeping them together frees
        the footer for keys alone.
        """
        topo = self._topo
        bits = [
            topo.model_name,
            f"{topo.n_cores}C/{topo.n_cpus}T",
            "hybrid" if topo.hybrid else "uniform",
            f"up {_fmt_duration(state.uptime)}",
        ]
        right = f"Interval {state.interval:.1f}s"
        budget = geom.line_width - len(right) - 2
        sep = f" {Glyph.DOT} "

        # Drop trailing segments rather than cutting a word in half.
        while len(bits) > 1 and 1 + len(sep.join(bits)) > budget:
            bits.pop()
        left = " " + sep.join(bits)
        if len(left) > max(0, budget):
            left = left[: max(0, budget)]

        gap = geom.line_width - len(left) - len(right)
        if gap < 1:
            return Theme.SUBTITLE + left[: geom.line_width] + Theme.RESET
        return (
            Theme.SUBTITLE + left + Theme.RESET + " " * gap
            + Theme.FOOTER_INFO + right + Theme.RESET
        )

    def _aggregate_specs(self, geom: Geometry) -> List[RowSpec]:
        specs = [
            RowSpec(
                cpu_text="TOTAL",
                type_text=f"{self._topo.n_cpus}T",
                type_colour=Theme.COUNT,
                series_key=SeriesKey.TOTAL,
                emphasise=True,
                marker_label=True,
            ),
        ]
        if geom.summary_rows > 1:
            for klass in self._topo.classes:
                members = self._topo.cpus_of_class(klass)
                specs.append(
                    RowSpec(
                        cpu_text=klass.value,
                        type_text=f"{len(members)}T",
                        type_colour=Theme.COUNT,
                        series_key=SeriesKey.klass(klass),
                    )
                )
        return specs

    @staticmethod
    def _capacity_label(total_bytes: int) -> str:
        """Size for the TYPE column: "32GB", falling back to "128G"."""
        gib = total_bytes / (1024 ** 3)
        text = f"{gib:.0f}GB"
        return text if len(text) <= W_TYPE else f"{gib:.0f}G"[:W_TYPE]

    @classmethod
    def _backing_spec(cls, state: "UiState") -> Optional[RowSpec]:
        mem = state.memory
        if mem is None or not mem.has_backing:
            return None
        return RowSpec(
            cpu_text=MemoryInfo.BACKING_ROW_LABEL[:W_CPU],
            type_text=cls._capacity_label(mem.backing_total),
            type_colour=Theme.COUNT,
            series_key=SeriesKey.BACKING,
            emphasise=True,
            palette=PALETTE_MEM,
        )

    @staticmethod
    def _memory_spec(state: "UiState") -> RowSpec:
        mem = state.memory
        if mem is None or mem.total == 0:
            return RowSpec(
                "RAM", "--", Theme.COUNT, SeriesKey.MEMORY, True,
                palette=PALETTE_MEM,
            )

        return RowSpec(
            cpu_text="RAM",
            type_text=Renderer._capacity_label(mem.total),
            type_colour=Theme.COUNT,
            series_key=SeriesKey.MEMORY,
            emphasise=True,
            palette=PALETTE_MEM,
        )

    def _body_specs(self, geom: Geometry) -> List[RowSpec]:
        if geom.row_mode is RowMode.PER_CPU:
            return [
                RowSpec(
                    cpu_text=str(cpu.lp_id),
                    type_text=cpu.type_tag,
                    type_colour=Theme.class_colour(cpu.type_tag),
                    series_key=SeriesKey.cpu(cpu.index),
                )
                for cpu in self._topo.cpus
            ]
        if geom.row_mode is RowMode.PER_GROUP:
            specs: List[RowSpec] = []
            for klass, buckets in core_buckets(self._topo, geom.group_size):
                for bucket_index, cores in enumerate(buckets):
                    specs.append(
                        RowSpec(
                            cpu_text=bucket_label(cores)[:W_CPU],
                            type_text=klass.value,
                            type_colour=Theme.class_colour(klass.value),
                            series_key=(
                                SeriesKey.core(cores[0].core_id)
                                if geom.group_size == 1
                                else SeriesKey.group(
                                    geom.group_size, klass.value, bucket_index
                                )
                            ),
                        )
                    )
            return specs
        return []

    def _column_head(self, geom: Geometry) -> str:
        sep = Theme.RULE + Glyph.V + Theme.RESET + Theme.COLUMN_HEAD
        parts = [Theme.COLUMN_HEAD, " ", "CPU".rjust(W_CPU)]
        if geom.show_type:
            parts += [" ", sep, " ", "TYPE".ljust(W_TYPE)]
        parts += [" ", sep, " ", "USAGE".rjust(W_USAGE)]
        if geom.show_gauge:
            parts += [" ", sep, " ", GAUGE_SCALE_LABEL]
        if geom.show_history:
            # The axis must span exactly the columns that hold data, and the
            # duration must be measured over exactly the samples drawn there.
            # Stretching it across the empty left margin would claim that the
            # whole row covers the stated time, which it does not until the
            # history has filled the window.
            plan = self._plan(geom)
            avail = max(0, geom.history_width - plan.pad)
            span = self._history.span(SeriesKey.TOTAL, plan.samples)
            window = _fmt_window(span) if span > 0 else ""
            axis = self._axis_bar(avail, window)
            axis = (" " * plan.pad + axis).ljust(geom.history_width)
            parts += [" ", sep, " ", Theme.AXIS, axis[:geom.history_width]]
        parts.append(Theme.RESET)
        return "".join(parts)

    @staticmethod
    def _axis_bar(avail: int, window: str) -> str:
        """A double-headed ruler with the span centred inside it.

            <--- History 20s --------------->

        The bar covers exactly the columns that hold data, so its two heads
        mark the oldest and the newest sample. The label is centred within
        that span, which means it settles at the middle of the column once
        the history has filled the window.
        """
        if avail <= 0:
            return ""
        candidates = [f"History {window}", window] if window else ["History"]

        # The ruler heads carry the meaning, the word does not: give up the
        # label before giving up the arrows.
        for text in candidates:
            label = f" {text} "
            if avail >= len(label) + 2:
                rest = avail - len(label) - 2
                left = rest // 2
                return (
                    Glyph.ARROW_LEFT + Glyph.H * left + label
                    + Glyph.H * (rest - left) + Glyph.ARROW
                )
        for text in candidates:
            if avail >= len(text):
                return text.center(avail)
        return " " * avail

    def _column_rule(self, geom: Geometry, junction: str = "") -> str:
        """Rule aligned to the vertical bars of the data rows.

        Each column contributes its width plus the two spaces that flank the
        bar; the last column has no trailing space, hence the -1. `junction`
        selects the glyph where the rule meets a column separator: a cross
        inside the table, a tee at its top and bottom edges.
        """
        junction = junction or Glyph.CROSS
        widths = [W_CPU]
        if geom.show_type:
            widths.append(W_TYPE)
        widths.append(W_USAGE)
        if geom.show_gauge:
            widths.append(W_GAUGE)
        if geom.show_history:
            widths.append(geom.history_width)

        segments = [w + 2 for w in widths]
        segments[-1] -= 1
        return (
            Theme.RULE
            + junction.join(Glyph.H * n for n in segments)
            + Theme.RESET
        )

    def _plan(self, geom: Geometry) -> TrendPlan:
        """Trend layout for this frame, computed once and shared by all rows.

        Every row must use the identical plan, otherwise the time axis would
        differ between rows and the seams would not line up.
        """
        if self._plan_cache_key != geom.history_width or self._plan_cache is None:
            self._plan_cache = self._history.cell_plan(geom.history_width)
            self._plan_cache_key = geom.history_width
        return self._plan_cache

    def invalidate_plan(self) -> None:
        self._plan_cache = None

    def _render_row(self, spec: RowSpec, geom: Geometry) -> str:
        sep = Theme.RULE + Glyph.V + Theme.RESET
        value = self._history.latest(spec.series_key)
        label_colour = Theme.TOTAL_LABEL if spec.emphasise else Theme.LABEL

        parts = [" ", label_colour, spec.cpu_text.rjust(W_CPU), Theme.RESET]
        if geom.show_type:
            parts += [
                " ", sep, " ",
                spec.type_colour, spec.type_text.ljust(W_TYPE), Theme.RESET,
            ]
        parts += [
            " ", sep, " ",
            Theme.USAGE, _fmt_percent(value).rjust(W_USAGE), Theme.RESET,
        ]
        if geom.show_gauge:
            parts += [" ", sep, " ", GAUGE.render(value, spec.palette)]
        if geom.show_history:
            parts += [" ", sep, " "]
            plan = self._plan(geom)
            samples = self._history.tail(spec.series_key, plan.samples)
            parts.append(
                SPARK.render(
                    plan.cells, samples, spec.palette, Theme.MARKER,
                    spec.marker_label,
                )
            )
        return "".join(parts)

    def _footer(self, geom: Geometry, state: "UiState") -> str:
        """Composed from whole segments only.

        Styled text must never be sliced: a cut inside an escape sequence
        leaks raw bytes onto the screen. Segments are therefore added while
        they fit and dropped entirely otherwise.
        """
        width = geom.line_width

        def styled_key(num: str, label: str) -> Tuple[str, int]:
            return (
                Theme.KEY_NUM + num + Theme.RESET
                + Theme.KEY_LABEL + label + Theme.RESET,
                len(num) + len(label),
            )

        hint_defs = [("F1", "Help"), ("F2", "Faster"), ("F3", "Slower"), ("Esc", "Quit")]
        hints: List[str] = []
        used = 1  # leading space
        for num, label in hint_defs:
            styled, size = styled_key(num, label + " ")
            if used + size > width:
                break
            hints.append(styled)
            used += size
        left = " " + "".join(hints)
        left_len = used

        # Keys only: the type legend lives in the help screen, where every
        # possible value can be listed without crowding the dashboard.
        return left


# =============================================================================
# 12  WORKERS
# =============================================================================


@dataclass
class UiState:
    interval: float = 1.0
    uptime: float = 0.0
    help_visible: bool = False
    help_scroll: int = 0
    running: bool = True
    memory: Optional[MemoryInfo] = None


# Sampling interval: a linear 0.1 s step between two hard bounds. Values are
# rounded to one decimal at every change so repeated F2/F3 presses cannot
# accumulate binary float drift (0.7000000000000001 and friends).
INTERVAL_MIN = 0.5
INTERVAL_MAX = 10.0
INTERVAL_STEP = 0.1


def clamp_interval(value: float) -> float:
    return round(min(INTERVAL_MAX, max(INTERVAL_MIN, value)), 1)


class MetricCollector(ABC):
    """A source of named time-series values.

    Each collector declares the series it owns and how often it wants to run,
    expressed as a multiple of the base tick. Today both collectors run every
    tick; the indirection is what will allow, say, memory to be sampled once a
    second while the CPU runs at 100 ms.
    """

    name: str = "collector"
    every_n_ticks: int = 1

    @abstractmethod
    def series_keys(self) -> List[str]: ...

    @abstractmethod
    def collect(self) -> Dict[str, float]: ...


class CpuCollector(MetricCollector):
    name = "cpu"

    def __init__(self, sampler: CpuSampler, topology: Topology) -> None:
        self._sampler = sampler
        self._topo = topology

        # Aggregation plan, precomputed so the hot loop only sums.
        self._cpu_keys = [SeriesKey.cpu(c.index) for c in topology.cpus]
        self._plan: List[Tuple[str, Tuple[int, ...]]] = []
        for core in topology.cores:
            self._plan.append(
                (SeriesKey.core(core.core_id), tuple(c.index for c in core.cpus))
            )
        for klass in topology.classes:
            self._plan.append(
                (
                    SeriesKey.klass(klass),
                    tuple(c.index for c in topology.cpus_of_class(klass)),
                )
            )
        # Every folding level the layout may ask for is maintained at all
        # times, so resizing the window never discards a row's history.
        for size in GROUP_SIZES:
            if size == 1:
                continue
            for klass, buckets in core_buckets(topology, size):
                for i, cores in enumerate(buckets):
                    members = tuple(c.index for core in cores for c in core.cpus)
                    self._plan.append(
                        (SeriesKey.group(size, klass.value, i), members)
                    )

    def series_keys(self) -> List[str]:
        return [SeriesKey.TOTAL] + self._cpu_keys + [k for k, _ in self._plan]

    def collect(self) -> Dict[str, float]:
        values = self._sampler.sample()
        out: Dict[str, float] = {}
        for i, key in enumerate(self._cpu_keys):
            out[key] = values[i] if i < len(values) else 0.0
        for key, members in self._plan:
            out[key] = sum(values[i] for i in members) / len(members)
        out[SeriesKey.TOTAL] = sum(values) / len(values) if values else 0.0
        return out


class MemoryCollector(MetricCollector):
    name = "memory"

    def __init__(self, backend: PlatformBackend, state: "UiState") -> None:
        self._backend = backend
        self._state = state

    def series_keys(self) -> List[str]:
        return [SeriesKey.MEMORY, SeriesKey.BACKING]

    def collect(self) -> Dict[str, float]:
        info = self._backend.read_memory()
        self._state.memory = info
        out = {SeriesKey.MEMORY: info.percent}
        if info.has_backing:
            out[SeriesKey.BACKING] = info.backing_percent
        return out


class SamplerWorker(threading.Thread):
    """Drives every collector on a steady base cadence."""

    def __init__(
        self,
        collectors: Sequence[MetricCollector],
        history: HistoryStore,
        state: "UiState",
        on_sample: Callable[[], None],
    ) -> None:
        super().__init__(name="sampler", daemon=True)
        self._collectors = list(collectors)
        self._history = history
        self._state = state
        self._on_sample = on_sample
        self._stop = threading.Event()
        # Interrupts the sleep between samples. Without it a cadence change
        # would only take effect when the current sleep expired, i.e. up to a
        # full old interval later — nine seconds at the slowest setting.
        self._nudge = threading.Event()
        self._tick = 0

    def stop(self) -> None:
        self._stop.set()
        self._nudge.set()

    def resync(self) -> None:
        """Wake the sampler so a new interval takes effect at once."""
        self._nudge.set()

    def run(self) -> None:
        interval = self._state.interval
        next_tick = time.monotonic() + interval
        while not self._stop.is_set():
            delay = next_tick - time.monotonic()
            if delay > 0:
                woken = self._nudge.wait(delay)
                self._nudge.clear()
                if self._stop.is_set():
                    break
                if woken:
                    if self._state.interval != interval:
                        interval = self._state.interval
                        next_tick = time.monotonic() + interval
                    continue  # either rescheduled, or a spurious wake
            self._tick += 1
            self.collect_once()
            self._on_sample()
            interval = self._state.interval
            next_tick += interval
            now = time.monotonic()
            if next_tick < now:
                next_tick = now + interval  # fell behind; resynchronise

    def collect_once(self) -> None:
        """One pass over the due collectors. Also used by the self-test."""
        stamp = time.monotonic()
        merged: Dict[str, float] = {}
        for collector in self._collectors:
            if self._tick % max(1, collector.every_n_ticks):
                continue
            try:
                merged.update(collector.collect())
            except Exception:
                continue  # a failing source must not stop the others
        if merged:
            self._history.push(merged, at=stamp)


class InputWorker(threading.Thread):
    """Reads keys on a dedicated thread so the render loop never polls."""

    def __init__(
        self,
        terminal: TerminalBackend,
        sink: "queue.Queue[str]",
        wake: threading.Event,
    ) -> None:
        super().__init__(name="input", daemon=True)
        self._terminal = terminal
        self._sink = sink
        self._wake = wake
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                key = self._terminal.read_key(0.2)
            except Exception:
                return
            if key:
                self._sink.put(key)
                # Queueing is not enough: the render loop is asleep until a
                # sample or the clock is due, so without this the key would
                # sit unread for up to a full second.
                self._wake.set()


# =============================================================================
# 13  APPLICATION
# =============================================================================


class Application:
    def __init__(self, backend: PlatformBackend, interval: float) -> None:
        self._backend = backend
        self._topology = backend.read_topology()
        self._sampler = backend.create_sampler()
        self._validate()

        self._state = UiState(interval=clamp_interval(interval))
        self._history = HistoryStore(MAX_HISTORY)
        self._collectors: List[MetricCollector] = [
            CpuCollector(self._sampler, self._topology),
            MemoryCollector(backend, self._state),
        ]
        for collector in self._collectors:
            self._history.ensure_many(collector.series_keys())
        self._renderer = Renderer(self._topology, self._history)
        self._state.memory = backend.read_memory()
        self._layout = LayoutSolver(
            self._topology, has_backing=self._state.memory.has_backing
        )
        self._terminal = backend.create_terminal()

        self._sampler_worker: Optional[SamplerWorker] = None
        self._keys: "queue.Queue[str]" = queue.Queue()
        # _tick says "a new sample landed"; _wake says "stop sleeping, there
        # is something to do". Sampling sets both, input only the latter.
        self._tick = threading.Event()
        self._wake = threading.Event()
        self._last_size: Tuple[int, int] = (0, 0)
        self._geom: Optional[Geometry] = None

    # -- setup ---------------------------------------------------------------

    def _validate(self) -> None:
        """The sampler indexes by position; the model must match it exactly."""
        n_model = self._topology.n_cpus
        n_sampler = self._sampler.count()
        if n_model != n_sampler:
            raise PlatformError(
                f"topology reports {n_model} logical processors but the "
                f"sampler reports {n_sampler}; refusing to display data that "
                "could be attributed to the wrong core"
            )
        for expected, cpu in enumerate(self._topology.cpus):
            if cpu.index != expected:
                raise PlatformError(
                    f"non-contiguous processor indices at position {expected}"
                )

    # -- main loop ------------------------------------------------------------

    def run(self) -> int:
        self._terminal.setup()
        self._terminal.write(f"{CSI}?25l")  # hide cursor
        sampler_worker = SamplerWorker(
            self._collectors, self._history, self._state, self._on_sample
        )
        self._sampler_worker = sampler_worker
        input_worker = InputWorker(self._terminal, self._keys, self._wake)
        try:
            sampler_worker.start()
            input_worker.start()
            self._loop()
        except KeyboardInterrupt:
            pass
        finally:
            sampler_worker.stop()
            input_worker.stop()
            self._sampler.close()
            # Leave a clean prompt: drop any lingering attribute, wipe the
            # screen, home the cursor, then make it visible again. Doing it
            # in this order means a terminal that ignores one sequence still
            # ends up in a sane state.
            self._terminal.write(
                f"{Theme.RESET}{CSI}2J{CSI}H{CSI}?25h"
            )
            self._terminal.flush()
            self._terminal.teardown()
        return 0

    @staticmethod
    def _next_second() -> float:
        """Monotonic deadline of the next wall-clock second boundary.

        Anchoring to time.time() (rather than to an arbitrary offset from
        startup) makes the displayed second change when the second actually
        changes. Recomputing it every tick, instead of adding 1.0, also
        absorbs NTP steps and resume-from-sleep without drifting.
        """
        return time.monotonic() + (1.0 - time.time() % 1.0)

    def _on_sample(self) -> None:
        self._tick.set()
        self._wake.set()

    def _loop(self) -> None:
        next_clock = self._next_second()
        while self._state.running:
            # Cleared before the work, not after: an event raised while this
            # iteration runs must survive and cause an immediate next pass.
            self._wake.clear()
            dirty = self._drain_keys()
            dirty |= self._check_resize()

            if self._tick.is_set():
                self._tick.clear()
                dirty = True

            self._state.uptime = self._backend.uptime_seconds()

            now = time.monotonic()
            if dirty:
                # A full frame redraws the clock too, but it must NOT reshape
                # the clock schedule: doing so would slave the 1 Hz update to
                # the sampling cadence and skip seconds whenever the interval
                # is longer than a second.
                self._paint_full()
            elif now >= next_clock:
                self._paint_clock()

            if now >= next_clock:
                next_clock = self._next_second()

            # Block until a sample lands, a key is pressed, or the clock is
            # due. No polling, so an idle monitor costs almost nothing.
            timeout = max(0.01, min(next_clock - time.monotonic(), 1.0))
            self._wake.wait(timeout)

    def _drain_keys(self) -> bool:
        dirty = False
        while True:
            try:
                key = self._keys.get_nowait()
            except queue.Empty:
                break
            dirty |= self._handle_key(key)
        return dirty

    def _handle_key(self, key: str) -> bool:
        if key == "CTRL_C":
            self._state.running = False
            return False

        if self._state.help_visible:
            return self._handle_help_key(key)

        if key == "ESC":
            self._state.running = False
            return False
        if key == "F1":
            self._state.help_visible = True
            self._state.help_scroll = 0
            return True
        if key == "F2":
            return self._shift_interval(-1)
        if key == "F3":
            return self._shift_interval(+1)
        return False

    def _handle_help_key(self, key: str) -> bool:
        """Only navigation and closing; everything else is ignored.

        Closing on any key would make scrolling impossible to discover: the
        first arrow press would dismiss the page the user is trying to read.
        """
        if key in ("ESC", "F1", "q", "Q"):
            self._state.help_visible = False
            return True

        if self._geom is None:
            return False
        page = max(1, self._renderer.help_viewport(self._geom) - 1)
        steps = {"UP": -1, "DOWN": 1, "PGUP": -page, "PGDN": page}
        if key == "HOME":
            target = 0
        elif key == "END":
            target = self._renderer.help_max_scroll(self._geom)
        elif key in steps:
            target = self._state.help_scroll + steps[key]
        else:
            return False

        target = max(0, min(target, self._renderer.help_max_scroll(self._geom)))
        if target == self._state.help_scroll:
            return False
        self._state.help_scroll = target
        return True

    def _shift_interval(self, direction: int) -> bool:
        """direction -1 speeds up (shorter interval), +1 slows down."""
        current = self._state.interval
        new = clamp_interval(current + direction * INTERVAL_STEP)
        if new == current:
            return False  # already at a bound; nothing to redraw
        self._state.interval = new
        # The history is kept: spans are measured from timestamps, so the
        # reported duration stays correct across a cadence change. What the
        # eye cannot infer is where the cells stop being equally spaced, so
        # the break is marked on the trend instead of discarding the data.
        self._history.mark(f" {new:.1f} ")
        if self._sampler_worker is not None:
            self._sampler_worker.resync()
        return True

    def _check_resize(self) -> bool:
        size = self._terminal.size()
        if size == self._last_size:
            return False
        self._last_size = size
        self._geom = self._layout.solve(size[0], size[1])
        return True

    def _paint_full(self) -> None:
        if self._geom is None:
            self._check_resize()
        assert self._geom is not None
        self._terminal.write(self._renderer.frame(self._geom, self._state))
        self._terminal.flush()

    def _paint_clock(self) -> None:
        if self._geom is None or not self._geom.usable or self._state.help_visible:
            return
        text = self._renderer.clock_only(self._geom)
        if text:
            self._terminal.write(text)
            self._terminal.flush()





# =============================================================================
# 14  CLI
# =============================================================================


def _selftest(cols: int, rows: int, interval: float) -> int:
    """Render a single frame to stdout without touching terminal modes."""
    backend = create_backend()
    topology = backend.read_topology()
    sampler = backend.create_sampler()

    state = UiState(interval=interval, uptime=backend.uptime_seconds())
    history = HistoryStore(MAX_HISTORY)
    collectors: List[MetricCollector] = [
        CpuCollector(sampler, topology),
        MemoryCollector(backend, state),
    ]
    for collector in collectors:
        history.ensure_many(collector.series_keys())

    worker = SamplerWorker(collectors, history, state, lambda: None)
    for _ in range(30):
        time.sleep(0.01)
        worker.collect_once()

    geom = LayoutSolver(
        topology, has_backing=state.memory.has_backing if state.memory else False
    ).solve(cols, rows)
    renderer = Renderer(topology, history)

    print(
        f"backend={backend.name} cols={cols} rows={rows} "
        f"col_mode={geom.col_mode.value} row_mode={geom.row_mode.value} "
        f"group={geom.group_size} history={geom.history_width} "
        f"line={geom.line_width}"
    )
    sys.stdout.write(renderer.frame(geom, state).replace(f"{CSI}2J", ""))
    sys.stdout.write(Theme.RESET + "\n")
    return 0


def _probe() -> int:
    """Print low-level backend details. Useful when reporting a problem."""
    print(f"python        : {sys.version.split()[0]} ({8 * ctypes_pointer_size()}-bit)")
    print(f"platform      : {sys.platform}")
    backend = create_backend()
    print(f"backend       : {backend.name}")

    topo = backend.read_topology()
    print(f"model         : {topo.model_name}")
    print(f"cores/threads : {topo.n_cores}C/{topo.n_cpus}T")
    print(f"classes       : {[k.value for k in topo.classes]} (hybrid={topo.hybrid})")
    for core in topo.cores:
        members = " ".join(f"{c.lp_id}:{c.type_tag}" for c in core.cpus)
        print(f"  core {core.core_id:>2} {core.core_class.value:<4} {members}")

    mem = backend.read_memory()
    print(f"memory        : {mem.total // 2**20} MB total, "
          f"{mem.available // 2**20} MB available ({mem.percent:.1f}% used)")
    if mem.has_backing:
        print(f"{mem.backing_kind:<14}: {mem.backing_total // 2**20} MB total, "
              f"{mem.backing_used // 2**20} MB used ({mem.backing_percent:.1f}%) "
              f"[shown as {MemoryInfo.BACKING_ROW_LABEL}]")
    else:
        reason = "not reported on this platform" if sys.platform == "win32" \
            else "no swap configured"
        print(f"swap          : {reason}")

    sampler = backend.create_sampler()
    print(f"sampler count : {sampler.count()}")
    if hasattr(sampler, "stride"):
        print(f"record stride : {sampler.stride} bytes")
        print(f"buffer size   : {sampler.buffer_size} bytes")
    time.sleep(0.3)
    values = sampler.sample()
    print("sample        : " + " ".join(f"{v:.1f}" for v in values))
    sampler.close()
    return 0


def ctypes_pointer_size() -> int:
    import ctypes as _c
    return _c.sizeof(_c.c_void_p)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cpumon", description=f"{APP_NAME} {APP_VERSION}"
    )
    parser.add_argument(
        "-i", "--interval", type=float, default=1.0,
        help=f"initial sampling interval in seconds "
             f"({INTERVAL_MIN:.1f}-{INTERVAL_MAX:.1f}, clamped)",
    )
    parser.add_argument(
        "--selftest", nargs=2, metavar=("COLS", "ROWS"), type=int,
        help="render one frame at the given size and exit",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="print backend diagnostics and exit",
    )
    args = parser.parse_args(argv)

    try:
        if args.probe:
            return _probe()
        if args.selftest:
            return _selftest(args.selftest[0], args.selftest[1], args.interval)
        return Application(create_backend(), args.interval).run()
    except PlatformError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
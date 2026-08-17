"""Windows implementation of the platform API.

Topology  : GetLogicalProcessorInformationEx(RelationProcessorCore)
Sampling  : NtQuerySystemInformation(SystemProcessorPerformanceInformation)
Memory    : GlobalMemoryStatusEx
Input     : msvcrt, polled on the input thread

A 64-bit interpreter is required: the native structure layouts differ under
WOW64 and the offsets below are the x64 ones.
"""

from __future__ import annotations

import contextlib
import ctypes
import msvcrt
import struct
import sys
import time
import winreg
from typing import ClassVar, Final

from cpumon.backend.base import CpuSampler, PlatformBackend, TerminalBackend
from cpumon.core.errors import PlatformError
from cpumon.core.model import CoreClass, MemoryInfo, Topology
from cpumon.core.topology import build_topology

# msvcrt, winreg and ctypes.WinDLL exist on Windows and nowhere else, and
# typeshed says so: every call below is an error when the file is analysed as
# another platform. This guard states the same thing in code. A type checker
# asked to look at the module as Linux finds the rest of it unreachable and
# leaves it alone, and an accidental import at runtime gets one clear line
# instead of a traceback from the first missing symbol.
if sys.platform != "win32":  # pragma: no cover
    raise PlatformError("the Windows backend requires Windows")


_RELATION_PROCESSOR_CORE: Final = 0
_ERROR_INSUFFICIENT_BUFFER: Final = 122
_SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION: Final = 8
_ENABLE_VIRTUAL_TERMINAL_PROCESSING: Final = 0x0004
_STD_OUTPUT_HANDLE: Final = -11

# SYSTEM_LOGICAL_PROCESSOR_INFORMATION_EX, PROCESSOR_RELATIONSHIP variant
# (x64 layout):
#   0  DWORD Relationship
#   4  DWORD Size
#   8  BYTE  Flags
#   9  BYTE  EfficiencyClass
#  10  BYTE  Reserved[20]
#  30  WORD  GroupCount
#  32  GROUP_AFFINITY GroupMask[]   (16 bytes each on x64)
_CORE_HEADER: Final = struct.Struct("<IIBB20xH")
_GROUP_AFFINITY: Final = struct.Struct("<QH")
_GROUP_AFFINITY_STRIDE: Final = 16
_GROUP_MASK_OFFSET: Final = 32

# IdleTime, KernelTime, UserTime, the first three fields of
# SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION. KernelTime includes idle.
_CPU_TIMES: Final = struct.Struct("<qqq")


class MEMORYSTATUSEX(ctypes.Structure):
    """Layout of the structure filled in by GlobalMemoryStatusEx."""

    _fields_: ClassVar = [
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

    def read(self) -> list[tuple[int, list[int]]]:
        """Returns [(efficiency_class, [logical processor ids]), ...]."""
        fn = self._k32.GetLogicalProcessorInformationEx
        size = ctypes.c_ulong(0)
        fn(_RELATION_PROCESSOR_CORE, None, ctypes.byref(size))
        err = ctypes.get_last_error()
        if size.value == 0:
            raise PlatformError(f"GetLogicalProcessorInformationEx sizing failed (err={err})")
        if err != _ERROR_INSUFFICIENT_BUFFER:
            raise PlatformError(f"GetLogicalProcessorInformationEx unexpected error {err}")

        buf = ctypes.create_string_buffer(size.value)
        if not fn(_RELATION_PROCESSOR_CORE, buf, ctypes.byref(size)):
            raise PlatformError(
                f"GetLogicalProcessorInformationEx failed (err={ctypes.get_last_error()})"
            )

        cores = self._parse(buf.raw, size.value)
        if not cores:
            raise PlatformError("no processor cores reported by the OS")
        return cores

    @staticmethod
    def _parse(raw: bytes, end: int) -> list[tuple[int, list[int]]]:
        offset = 0
        cores: list[tuple[int, list[int]]] = []
        while offset + _CORE_HEADER.size <= end:
            rel, length, _flags, eff, groups = _CORE_HEADER.unpack_from(raw, offset)
            if length < 8 or offset + length > end:
                break
            if rel == _RELATION_PROCESSOR_CORE:
                lps: list[int] = []
                for g in range(groups):
                    mask, group = _GROUP_AFFINITY.unpack_from(
                        raw, offset + _GROUP_MASK_OFFSET + g * _GROUP_AFFINITY_STRIDE
                    )
                    lps.extend(group * 64 + bit for bit in range(64) if mask >> bit & 1)
                cores.append((eff, sorted(lps)))
            offset += length
        return cores


class WindowsSampler(CpuSampler):
    """Per-processor idle/kernel/user tick deltas from ntdll.

    Buffer sizing note: the kernel does NOT accept an arbitrarily large buffer
    for this information class. It requires a length that is an exact multiple
    of sizeof(SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION); anything else is
    rejected with STATUS_INFO_LENGTH_MISMATCH, even when the buffer is far
    bigger than needed. The size is therefore asked for rather than guessed,
    with a short list of known-plausible strides as a fallback if the probe
    yields nothing.
    """

    _MAX_CPUS: ClassVar[int] = 64  # one processor group
    # x64: five LARGE_INTEGER fields plus one ULONG, padded to 8 bytes.
    _STRIDE_CANDIDATES: ClassVar[tuple[int, ...]] = (48, 40, 56, 64, 32)
    _MIN_PLAUSIBLE_STRIDE: ClassVar[int] = 24

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
            raise PlatformError(f"unsupported active processor count {self._expected}")

        self._ret = ctypes.c_ulong(0)
        self._buf = ctypes.create_string_buffer(1)
        self._size = 0
        self._stride = 0
        self._negotiate_buffer()

        self._prev_idle = [0] * self._expected
        self._prev_busy = [0] * self._expected
        self.sample()  # prime the deltas

    # -- buffer negotiation --------------------------------------------------

    def _raw_query(self, buf: ctypes.Array[ctypes.c_char] | None, length: int) -> int:
        """Returns the raw NTSTATUS without raising."""
        return (
            int(
                self._nt.NtQuerySystemInformation(
                    _SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION,
                    buf,
                    length,
                    ctypes.byref(self._ret),
                )
            )
            & 0xFFFFFFFF
        )

    def _probe_required_length(self) -> int:
        """Ask the kernel how many bytes it wants."""
        self._ret.value = 0
        self._raw_query(None, 0)
        return int(self._ret.value)

    def _candidate_sizes(self) -> list[int]:
        sizes: list[int] = []
        probed = self._probe_required_length()
        if probed:
            sizes.append(probed)
        sizes.extend(stride * self._expected for stride in self._STRIDE_CANDIDATES)
        seen: set[int] = set()
        unique: list[int] = []
        for size in sizes:
            if size > 0 and size not in seen:
                seen.add(size)
                unique.append(size)
        return unique

    def _negotiate_buffer(self) -> None:
        attempts: list[str] = []
        for size in self._candidate_sizes():
            buf = ctypes.create_string_buffer(size)
            status = self._raw_query(buf, size)
            if status != 0:
                attempts.append(f"{size}B -> 0x{status:08X}")
                continue
            returned = int(self._ret.value)
            if returned == 0 or returned % self._expected:
                attempts.append(f"{size}B -> returned {returned}, not a multiple")
                continue
            stride = returned // self._expected
            if stride < self._MIN_PLAUSIBLE_STRIDE:
                attempts.append(f"{size}B -> implausible stride {stride}")
                continue
            self._buf, self._size, self._stride = buf, size, stride
            return
        raise PlatformError(
            "could not negotiate a buffer for "
            "NtQuerySystemInformation(SystemProcessorPerformanceInformation) "
            f"with {self._expected} processors; tried: " + "; ".join(attempts)
        )

    # -- sampling ------------------------------------------------------------

    def _query(self) -> int:
        status = self._raw_query(self._buf, self._size)
        if status != 0:
            raise PlatformError(
                f"NtQuerySystemInformation status=0x{status:08X} "
                f"(buffer {self._size}B, stride {self._stride})"
            )
        return int(self._ret.value)

    @property
    def stride(self) -> int:
        return self._stride

    @property
    def buffer_size(self) -> int:
        return self._size

    def count(self) -> int:
        return self._expected

    def sample(self) -> list[float]:
        self._query()
        raw = self._buf.raw
        out: list[float] = []
        for i in range(self._expected):
            idle, kernel, user = _CPU_TIMES.unpack_from(raw, i * self._stride)
            total = kernel + user
            d_idle = idle - self._prev_idle[i]
            d_total = total - self._prev_busy[i]
            self._prev_idle[i] = idle
            self._prev_busy[i] = total
            pct = 100.0 * (d_total - d_idle) / d_total if d_total > 0 else 0.0
            out.append(min(100.0, max(0.0, pct)))
        return out


class WindowsTerminal(TerminalBackend):
    """Console in virtual-terminal mode, with msvcrt for key input."""

    # msvcrt returns a prefix byte then a scan code for function keys.
    # Function keys arrive behind the \x00 prefix, navigation keys behind
    # \xe0 (and behind \x00 too, from the numpad with NumLock off). The
    # scan codes do not overlap, so one table covers both prefixes.
    _SCAN_MAP: ClassVar[dict[str, str]] = {
        ";": "F1", "<": "F2", "=": "F3", ">": "F4",
        "?": "F5", "@": "F6", "A": "F7", "B": "F8",
        "C": "F9", "D": "F10", "\x85": "F11", "\x86": "F12",
        "H": "UP", "P": "DOWN", "I": "PGUP", "Q": "PGDN",
        "G": "HOME", "O": "END",
    }  # fmt: skip
    _PREFIXES: ClassVar[tuple[str, ...]] = ("\x00", "\xe0")
    _POLL_INTERVAL: ClassVar[float] = 0.02

    def __init__(self) -> None:
        self._saved_mode: int | None = None

    def setup(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.GetStdHandle(_STD_OUTPUT_HANDLE)
        mode = ctypes.c_ulong()
        if k32.GetConsoleMode(handle, ctypes.byref(mode)):
            self._saved_mode = mode.value
            k32.SetConsoleMode(handle, mode.value | _ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        # Box drawing needs UTF-8. stdout is not always a reconfigurable
        # TextIOWrapper (it may be a pipe wrapper), hence the guarded call.
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8")

    def teardown(self) -> None:
        if self._saved_mode is None:
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.GetStdHandle(_STD_OUTPUT_HANDLE)
        k32.SetConsoleMode(handle, self._saved_mode)

    def read_key(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in self._PREFIXES:
                    return self._SCAN_MAP.get(msvcrt.getwch())
                if ch == "\x1b":
                    return "ESC"
                if ch == "\x03":
                    return "CTRL_C"
                return ch
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(self._POLL_INTERVAL, remaining))


class WindowsBackend(PlatformBackend):
    """Windows platform backend."""

    name = "windows"

    def __init__(self) -> None:
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise PlatformError(
                "a 64-bit Python interpreter is required: the native "
                "structure layouts differ under WOW64"
            )
        # One handle, reused: uptime and memory are read on every sample, and
        # loading kernel32 per call would dominate their cost.
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.GetTickCount64.argtypes = []
        self._k32.GetTickCount64.restype = ctypes.c_ulonglong

    def read_topology(self) -> Topology:
        raw_cores = WindowsTopologyReader().read()
        mapping = self._class_map(sorted({eff for eff, _ in raw_cores}))
        return build_topology(
            model_name=self._model_name(),
            raw_cores=[(mapping[eff], lps) for eff, lps in raw_cores],
        )

    @staticmethod
    def _class_map(eff_values: list[int]) -> dict[int, CoreClass]:
        """Map EfficiencyClass values onto core classes.

        A higher EfficiencyClass means higher relative performance. Two
        classes -> P / E. Three or more -> P / E / LPE.
        """
        if len(eff_values) == 1:
            return {eff_values[0]: CoreClass.P}
        if len(eff_values) == 2:
            return {eff_values[1]: CoreClass.P, eff_values[0]: CoreClass.E}
        mapping = {eff_values[-1]: CoreClass.P, eff_values[0]: CoreClass.LPE}
        for mid in eff_values[1:-1]:
            mapping[mid] = CoreClass.E
        return mapping

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
        return float(self._k32.GetTickCount64()) / 1000.0

    def read_memory(self) -> MemoryInfo:
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not self._k32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise PlatformError(f"GlobalMemoryStatusEx failed (err={ctypes.get_last_error()})")
        # No secondary metric is reported on Windows; see the note in
        # MemoryInfo for why the commit charge was measured and dropped.
        #
        # If a paging indicator is ever wanted here, the useful one is hard
        # faults per second, from NtQuerySystemInformation class 2
        # (SystemPerformanceInformation): its PageReadCount field is a
        # cumulative counter, so the same delta treatment used for the CPU
        # times applies. The structure is large and undocumented, so expect to
        # negotiate its size the way WindowsSampler does.
        return MemoryInfo(
            total=int(status.ullTotalPhys),
            available=int(status.ullAvailPhys),
        )

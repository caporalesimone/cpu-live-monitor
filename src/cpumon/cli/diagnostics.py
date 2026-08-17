"""Non-interactive commands: one rendered frame, and backend details.

Both are deliberately free of terminal mode changes, so their output can be
piped, diffed and pasted into a bug report.
"""

from __future__ import annotations

import contextlib
import struct
import sys
import time

from cpumon.backend import create_backend
from cpumon.core.model import MemoryInfo
from cpumon.runtime.session import MonitorSession
from cpumon.ui.model import Viewport
from cpumon.ui.renders.cli import CliRenderer, ansi
from cpumon.ui.renders.cli.theme import Theme

# Samples taken before the frame is drawn, so the trend has something in it.
_WARMUP_SAMPLES = 30
_WARMUP_DELAY = 0.01
_PROBE_SAMPLE_DELAY = 0.3
_MB = 2**20


def _use_utf8_stdout() -> None:
    """Make sure the box drawing survives a redirected stdout.

    A frame piped to a file or another process gets the locale encoding, which
    on Windows cannot represent the box characters at all. The interactive path
    does the same thing in the terminal backend.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(OSError, ValueError):
            reconfigure(encoding="utf-8")


def selftest(cols: int, rows: int, interval: float) -> int:
    """Render a single frame to stdout without touching terminal modes."""
    _use_utf8_stdout()
    session = MonitorSession.create(create_backend(), interval)
    session.refresh_uptime()

    worker = session.worker(lambda: None)
    for _ in range(_WARMUP_SAMPLES):
        time.sleep(_WARMUP_DELAY)
        worker.collect_once()

    # The CLI renderer by name, not by interface: this command exists to show
    # what the terminal frame looks like at a given size.
    renderer = CliRenderer(session.topology, has_backing=session.has_backing)
    plan = renderer.plan(
        Viewport(cols, rows), session.state, session.memory_info, session.markers()
    )
    geom = plan.geometry
    frame = renderer.render(plan, session.build(plan.request))

    print(
        f"backend={session.backend.name} cols={cols} rows={rows} "
        f"col_mode={geom.col_mode.value} row_mode={geom.row_mode.value} "
        f"group={geom.group_size} history={geom.history_width} "
        f"line={geom.line_width}"
    )
    # The screen wipe would scroll the frame out of a piped log.
    sys.stdout.write(frame.replace(ansi.CLEAR_SCREEN, ""))
    sys.stdout.write(Theme.RESET + "\n")
    return 0


def probe() -> int:
    """Print low-level backend details. Useful when reporting a problem."""
    bits = struct.calcsize("P") * 8
    print(f"python        : {sys.version.split()[0]} ({bits}-bit)")
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
    print(
        f"memory        : {mem.total // _MB} MB total, "
        f"{mem.available // _MB} MB available ({mem.percent:.1f}% used)"
    )
    if mem.has_backing:
        print(
            f"{mem.backing_kind:<14}: {mem.backing_total // _MB} MB total, "
            f"{mem.backing_used // _MB} MB used ({mem.backing_percent:.1f}%) "
            f"[shown as {MemoryInfo.BACKING_ROW_LABEL}]"
        )
    else:
        reason = (
            "not reported on this platform" if sys.platform == "win32" else "no swap configured"
        )
        print(f"swap          : {reason}")

    sampler = backend.create_sampler()
    print(f"sampler count : {sampler.count()}")
    stride = getattr(sampler, "stride", None)
    buffer_size = getattr(sampler, "buffer_size", None)
    if stride is not None and buffer_size is not None:
        print(f"record stride : {stride} bytes")
        print(f"buffer size   : {buffer_size} bytes")
    time.sleep(_PROBE_SAMPLE_DELAY)
    values = sampler.sample()
    print("sample        : " + " ".join(f"{v:.1f}" for v in values))
    sampler.close()
    return 0

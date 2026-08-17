"""Orchestration: wires the session, the workers and a renderer together.

The loop does no work beyond deciding *when* to repaint and sequencing the three
steps of a frame:

    plan    the renderer decides what fits, and what data that needs
    build   the session fills the request from the history store
    render  the renderer draws it

It depends on the :class:`~cpumon.ui.renderer.Renderer` interface only, so which
presentation is in use is a construction detail.
"""

from __future__ import annotations

import queue
import threading
import time

from cpumon.backend.base import PlatformBackend
from cpumon.core.history import HistoryStore
from cpumon.runtime.controller import InputController
from cpumon.runtime.keyboard import InputWorker
from cpumon.runtime.sampling import SamplerWorker
from cpumon.runtime.session import MonitorSession
from cpumon.ui.help import HelpContent
from cpumon.ui.model import Viewport
from cpumon.ui.renderer import Renderer
from cpumon.ui.renders import DEFAULT_RENDERER, create_renderer
from cpumon.ui.state import UiState

# Longest the loop will sleep with nothing to do. One second keeps the clock
# ticking even when the sampling interval is far longer.
_MAX_SLEEP = 1.0
_MIN_SLEEP = 0.01


def next_second() -> float:
    """Monotonic deadline of the next wall-clock second boundary.

    Anchoring to time.time() (rather than to an arbitrary offset from startup)
    makes the displayed second change when the second actually changes.
    Recomputing it every tick, instead of adding 1.0, also absorbs NTP steps and
    resume-from-sleep without drifting.
    """
    return time.monotonic() + (1.0 - time.time() % 1.0)


class Application:
    """The interactive monitor."""

    def __init__(
        self,
        backend: PlatformBackend,
        interval: float,
        renderer_name: str = DEFAULT_RENDERER,
    ) -> None:
        self._session = MonitorSession.create(backend, interval)
        self._terminal = backend.create_terminal()
        # One help document, shared: the controller scrolls exactly the page the
        # renderer draws.
        help_content = HelpContent()
        self._renderer: Renderer = create_renderer(
            renderer_name,
            topology=self._session.topology,
            has_backing=self._session.has_backing,
            help_content=help_content,
        )
        self._controller = InputController(
            self._session.state,
            help_content,
            on_interval_change=self._on_interval_change,
        )

        self._worker: SamplerWorker | None = None
        self._keys: queue.Queue[str] = queue.Queue()
        # _tick says "a new sample landed"; _wake says "stop sleeping, there is
        # something to do". Sampling sets both, input only the latter.
        self._tick = threading.Event()
        self._wake = threading.Event()
        self._viewport: Viewport | None = None

    @property
    def state(self) -> UiState:
        """What the user has asked the display to do."""
        return self._session.state

    @property
    def history(self) -> HistoryStore:
        """The samples behind the current frame."""
        return self._session.history

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> int:
        self._terminal.setup()
        self._terminal.write(self._renderer.begin())
        worker = self._session.worker(self._on_sample)
        self._worker = worker
        input_worker = InputWorker(self._terminal, self._keys, self._wake)
        try:
            worker.start()
            input_worker.start()
            self._loop()
        except KeyboardInterrupt:
            pass
        finally:
            worker.stop()
            input_worker.stop()
            self._session.sampler.close()
            # Whatever the renderer needs written to leave the device usable.
            self._terminal.write(self._renderer.end())
            self._terminal.flush()
            self._terminal.teardown()
        return 0

    # -- main loop -----------------------------------------------------------

    def _loop(self) -> None:
        state = self._session.state
        next_clock = next_second()
        while state.running:
            # Cleared before the work, not after: an event raised while this
            # iteration runs must survive and cause an immediate next pass.
            self._wake.clear()
            dirty = self._drain_keys()
            dirty |= self._check_resize()

            if self._tick.is_set():
                self._tick.clear()
                dirty = True

            now = time.monotonic()
            if dirty:
                # A full frame redraws the clock too, but it must NOT reshape the
                # clock schedule: doing so would slave the 1 Hz update to the
                # sampling cadence and skip seconds whenever the interval is
                # longer than a second.
                self._paint_full()
            elif now >= next_clock:
                self._paint_partial()

            if now >= next_clock:
                next_clock = next_second()

            if not state.running:
                # A quit key was drained above. Leaving now rather than sleeping
                # first is what makes Esc and Ctrl-C feel immediate: the wait
                # below would otherwise hold the app for up to a second.
                break

            # Block until a sample lands, a key is pressed, or the clock is due.
            # No polling, so an idle monitor costs almost nothing.
            timeout = max(_MIN_SLEEP, min(next_clock - time.monotonic(), _MAX_SLEEP))
            self._wake.wait(timeout)

    def _on_sample(self) -> None:
        self._tick.set()
        self._wake.set()

    def _on_interval_change(self, interval: float) -> None:
        # The history is kept: spans are measured from timestamps, so the
        # reported duration stays correct across a cadence change. What the eye
        # cannot infer is where the cells stop being equally spaced, so the break
        # is marked on the trend instead of discarding the data.
        self._session.history.mark(f" {interval:.1f} ")
        if self._worker is not None:
            self._worker.resync()

    # -- input ---------------------------------------------------------------

    def _drain_keys(self) -> bool:
        dirty = False
        while True:
            try:
                key = self._keys.get_nowait()
            except queue.Empty:
                break
            dirty |= self._controller.handle(key, self._viewport)
        return dirty

    def _check_resize(self) -> bool:
        cols, rows = self._terminal.size()
        viewport = Viewport(cols, rows)
        if viewport == self._viewport:
            return False
        self._viewport = viewport
        return True

    # -- painting ------------------------------------------------------------

    def _paint_full(self) -> None:
        if self._viewport is None:
            self._check_resize()
        viewport = self._viewport
        if viewport is None:  # pragma: no cover - _check_resize always sets it
            return
        self._session.refresh_uptime()
        plan = self._renderer.plan(
            viewport,
            self._session.state,
            self._session.memory_info,
            self._session.markers(),
        )
        model = self._session.build(plan.request)
        self._terminal.write(self._renderer.render(plan, model))
        self._terminal.flush()

    def _paint_partial(self) -> None:
        """Whatever the renderer can refresh on its own, typically the clock."""
        text = self._renderer.partial_update()
        if text:
            self._terminal.write(text)
            self._terminal.flush()

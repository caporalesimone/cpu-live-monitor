"""The input thread: reads keys so the render loop never polls."""

from __future__ import annotations

import queue
import threading
from typing import ClassVar

from cpumon.backend.base import TerminalBackend


class InputWorker(threading.Thread):
    """Blocks on the terminal and queues whatever the user pressed."""

    _READ_TIMEOUT: ClassVar[float] = 0.2

    def __init__(
        self,
        terminal: TerminalBackend,
        sink: queue.Queue[str],
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
                key = self._terminal.read_key(self._READ_TIMEOUT)
            except Exception:  # a dead input source ends the thread, quietly
                return
            if key:
                self._sink.put(key)
                # Queueing is not enough: the render loop is asleep until a
                # sample or the clock is due, so without this the key would sit
                # unread for up to a full second.
                self._wake.set()

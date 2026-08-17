"""The sampling thread: drives every collector on a steady base cadence."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from cpumon.core.collectors import MetricCollector
from cpumon.core.history import HistoryStore


class SamplerWorker(threading.Thread):
    """Runs the collectors and pushes their readings into the history store."""

    def __init__(
        self,
        collectors: Sequence[MetricCollector],
        history: HistoryStore,
        interval: Callable[[], float],
        on_sample: Callable[[], None],
    ) -> None:
        super().__init__(name="sampler", daemon=True)
        self._collectors = list(collectors)
        self._history = history
        self._interval = interval
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
        interval = self._interval()
        next_tick = time.monotonic() + interval
        while not self._stop.is_set():
            delay = next_tick - time.monotonic()
            if delay > 0:
                woken = self._nudge.wait(delay)
                self._nudge.clear()
                if self._stop.is_set():
                    break
                if woken:
                    if self._interval() != interval:
                        interval = self._interval()
                        next_tick = time.monotonic() + interval
                    continue  # either rescheduled, or a spurious wake
            self._tick += 1
            self.collect_once()
            self._on_sample()
            interval = self._interval()
            next_tick += interval
            now = time.monotonic()
            if next_tick < now:
                next_tick = now + interval  # fell behind; resynchronise

    def collect_once(self) -> None:
        """One pass over the due collectors. Also used by the self-test."""
        stamp = time.monotonic()
        merged: dict[str, float] = {}
        for collector in self._collectors:
            if self._tick % max(1, collector.every_n_ticks):
                continue
            try:
                merged.update(collector.collect())
            except Exception:  # a failing source must not stop the others
                continue
        if merged:
            self._history.push(merged, at=stamp)

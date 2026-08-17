"""Ring buffers for the time series behind every row.

Each metric owns an independent :class:`TimeSeries`. A series records the
wall-clock instant of every sample alongside its value, so the displayed time
span is *measured*, never inferred from a global interval. That is what makes
the time base decoupled: two series may be fed at different cadences, or by
different collectors, and each still reports its own honest duration.

The groundwork for a fixed-duration window (for example "always show the last
10 minutes regardless of interval") is the ``window_seconds`` field: when set,
:meth:`TimeSeries.tail_for_width` resamples the series onto the requested
number of cells instead of taking the last N raw samples. Nothing sets it yet.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from cpumon.core.model import CoreClass

_MAX_MARKERS = 32


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


@dataclass(frozen=True)
class SeriesReading:
    """One series as it stood at a single instant."""

    latest: float = 0.0
    samples: tuple[float, ...] = ()


_EMPTY_READING = SeriesReading()


@dataclass(frozen=True)
class HistoryReading:
    """Several series, all read inside one critical section.

    Consistency across series is the point. An aggregate is only ever the mean of
    its members if both were taken from the same sample: read one series before a
    push and another after it, and the totals on screen stop adding up.
    """

    series: Mapping[str, SeriesReading]
    span: float = 0.0

    def of(self, key: str) -> SeriesReading:
        return self.series.get(key, _EMPTY_READING)


@dataclass(frozen=True)
class MarkerState:
    """Everything the trend layout needs about the shape of the history.

    Positions are expressed in samples, not seconds, exactly like the
    sparkline cells they annotate. Taken atomically so the sequence number,
    the sample count and the markers can never describe different instants.
    """

    sequence: int
    count: int
    markers: tuple[tuple[int, str], ...] = field(default=())


class TimeSeries:
    """A ring buffer of timestamped samples with its own time base.

    Not thread-safe on its own; :class:`HistoryStore` provides the lock.
    """

    __slots__ = ("_stamps", "_values", "capacity", "window_seconds")

    def __init__(self, capacity: int, window_seconds: float | None = None) -> None:
        self.capacity = capacity
        # None  -> one cell per sample (current behaviour)
        # float -> one cell per (window_seconds / cells), resampled
        self.window_seconds = window_seconds
        self._values: deque[float] = deque(maxlen=capacity)
        self._stamps: deque[float] = deque(maxlen=capacity)

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

    def tail_for_width(self, cells: int) -> list[float]:
        """The values to draw in *cells* horizontal positions, oldest first."""
        if cells <= 0 or not self._values:
            return []
        if self.window_seconds is None:
            return list(self._values)[-cells:]
        return self._resample(cells, self.window_seconds)

    def span_for_width(self, cells: int) -> float:
        """Wall-clock duration actually covered by ``tail_for_width(cells)``.

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

    def _resample(self, cells: int, window: float) -> list[float]:
        """Bucket the last *window* seconds into *cells* averages.

        Reserved for the fixed-duration mode; kept here so the renderer never
        needs to know which mode a series is in.
        """
        now = self._stamps[-1]
        start = now - window
        step = window / cells
        buckets: list[list[float]] = [[] for _ in range(cells)]
        for value, stamp in zip(self._values, self._stamps, strict=True):
            if stamp < start:
                continue
            idx = min(cells - 1, int((stamp - start) / step))
            buckets[idx].append(value)
        out: list[float] = []
        carry = 0.0
        for bucket in buckets:
            if bucket:
                carry = sum(bucket) / len(bucket)
            out.append(carry)  # hold the last known value across empty buckets
        return out


class HistoryStore:
    """Thread-safe collection of named :class:`TimeSeries`.

    The store owns the values and the time-base markers. How those turn into
    screen cells is a rendering concern and lives in the UI layer.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._series: dict[str, TimeSeries] = {}
        # Every push advances _seq; a marker pins a label to the sample that
        # will be pushed next, which is the first one taken at the new cadence.
        self._seq = 0
        self._count = 0  # samples actually held (capped by capacity)
        self._markers: deque[tuple[int, str]] = deque(maxlen=_MAX_MARKERS)

    @property
    def capacity(self) -> int:
        return self._capacity

    def ensure(self, key: str, window_seconds: float | None = None) -> None:
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
            keep = [(seq, text) for seq, text in self._markers if self._seq - seq > len(label)]
            keep.append((self._seq, label))
            self._markers = deque(keep, maxlen=_MAX_MARKERS)

    def marker_state(self) -> MarkerState:
        """Atomic snapshot of the sample counters and the pinned markers."""
        with self._lock:
            return MarkerState(self._seq, self._count, tuple(self._markers))

    def push(self, values: Mapping[str, float], at: float | None = None) -> None:
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

    def read(self, keys: Iterable[str], samples: int = 0, span_key: str = "") -> HistoryReading:
        """Every series a frame needs, taken as one consistent set.

        One lock for the whole frame rather than one per series: a sample landing
        between two reads would leave the aggregates describing one instant and
        the rows another, and the percentages would no longer add up.
        """
        with self._lock:
            readings = {key: self._reading(self._series.get(key), samples) for key in keys}
            span = 0.0
            if span_key and samples:
                series = self._series.get(span_key)
                span = series.span_for_width(samples) if series else 0.0
            return HistoryReading(readings, span)

    @staticmethod
    def _reading(series: TimeSeries | None, samples: int) -> SeriesReading:
        """Caller must hold the lock."""
        if series is None:
            return _EMPTY_READING
        return SeriesReading(
            latest=series.latest,
            samples=tuple(series.tail_for_width(samples)) if samples else (),
        )

    def tail(self, key: str, width: int) -> list[float]:
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

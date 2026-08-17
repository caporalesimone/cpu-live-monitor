"""History: values, measured spans and the markers that flag cadence changes."""

from __future__ import annotations

import threading

from cpumon.core.history import HistoryStore, SeriesKey, TimeSeries
from cpumon.core.model import CoreClass


def test_series_keys_are_namespaced() -> None:
    assert SeriesKey.klass(CoreClass.E) == "cpu:class:E"
    assert SeriesKey.core(3) == "cpu:core:3"
    assert SeriesKey.cpu(7) == "cpu:lp:7"
    assert SeriesKey.group(4, "P", 2) == "cpu:grp4:P:2"
    # No key may collide with another kind of key.
    keys = {
        SeriesKey.TOTAL,
        SeriesKey.MEMORY,
        SeriesKey.BACKING,
        SeriesKey.klass(CoreClass.P),
        SeriesKey.core(1),
        SeriesKey.cpu(1),
        SeriesKey.group(2, "P", 1),
    }
    assert len(keys) == 7


def test_timeseries_keeps_only_the_capacity() -> None:
    series = TimeSeries(3)
    for i in range(10):
        series.append(float(i), at=float(i))
    assert len(series) == 3
    assert series.latest == 9.0
    assert series.tail_for_width(10) == [7.0, 8.0, 9.0]


def test_tail_takes_the_newest_samples() -> None:
    series = TimeSeries(10)
    for i in range(5):
        series.append(float(i), at=float(i))
    assert series.tail_for_width(3) == [2.0, 3.0, 4.0]
    assert series.tail_for_width(0) == []


def test_span_covers_the_oldest_bucket_from_its_start() -> None:
    series = TimeSeries(10)
    for i in range(5):
        series.append(1.0, at=float(i))  # stamps 0..4, one second apart
    # Three visible cells end at t=4 and start at t=1: the sample before the
    # oldest visible one is still buffered, so the span is exactly 3 s.
    assert series.span_for_width(3) == 3.0


def test_span_extrapolates_when_nothing_older_is_retained() -> None:
    series = TimeSeries(3)
    for i in range(3):
        series.append(1.0, at=float(i))
    # Stamps 0,1,2: two gaps of 1 s over three cells -> 3 s, not 2 s.
    assert series.span_for_width(3) == 3.0


def test_span_of_a_single_sample_is_unknown() -> None:
    series = TimeSeries(5)
    series.append(1.0, at=10.0)
    assert series.span_for_width(5) == 0.0


def test_store_creates_series_on_demand() -> None:
    store = HistoryStore(10)
    store.push({"a": 1.0}, at=1.0)
    assert store.latest("a") == 1.0
    assert store.depth("a") == 1
    assert store.latest("missing") == 0.0
    assert store.tail("missing", 5) == []
    assert store.span("missing", 5) == 0.0


def test_marker_state_tracks_pushes() -> None:
    store = HistoryStore(4)
    for i in range(6):
        store.push({"a": float(i)}, at=float(i))
    state = store.marker_state()
    assert state.sequence == 6
    assert state.count == 4  # capped by capacity


def test_marker_is_pinned_to_the_next_sample() -> None:
    store = HistoryStore(10)
    store.push({"a": 0.0}, at=0.0)
    store.mark(" 2.0 ")
    assert store.marker_state().markers == ((1, " 2.0 "),)


def test_close_markers_are_dropped_so_labels_cannot_overlap() -> None:
    store = HistoryStore(50)
    store.mark(" 1.0 ")  # pinned at sequence 0
    store.push({"a": 0.0}, at=0.0)
    store.mark(" 2.0 ")  # only one sample later: too close for a 5-char label
    assert store.marker_state().markers == ((1, " 2.0 "),)


def test_distant_markers_are_kept() -> None:
    store = HistoryStore(50)
    store.mark(" 1.0 ")
    for i in range(10):
        store.push({"a": 0.0}, at=float(i))
    store.mark(" 2.0 ")
    assert store.marker_state().markers == ((0, " 1.0 "), (10, " 2.0 "))


def test_markers_expire_with_the_samples_they_annotate() -> None:
    store = HistoryStore(3)
    store.mark(" 1.0 ")
    for i in range(10):
        store.push({"a": 0.0}, at=float(i))
    assert store.marker_state().markers == ()


def test_read_matches_the_individual_accessors() -> None:
    store = HistoryStore(50)
    for i in range(20):
        store.push({"a": float(i), "b": float(i) * 2}, at=float(i))

    reading = store.read(["a", "b"], samples=5, span_key="a")
    for key in ("a", "b"):
        assert reading.of(key).latest == store.latest(key)
        assert list(reading.of(key).samples) == store.tail(key, 5)
    assert reading.span == store.span("a", 5)


def test_read_of_an_unknown_series_is_empty() -> None:
    store = HistoryStore(10)
    store.push({"a": 1.0}, at=1.0)
    reading = store.read(["a", "ghost"], samples=3)
    assert reading.of("ghost").latest == 0.0
    assert reading.of("ghost").samples == ()
    assert reading.of("never asked for").latest == 0.0


def test_read_without_samples_still_gives_the_latest() -> None:
    store = HistoryStore(10)
    store.push({"a": 7.0}, at=1.0)
    reading = store.read(["a"])
    assert reading.of("a").latest == 7.0
    assert reading.of("a").samples == ()
    assert reading.span == 0.0


def test_read_is_consistent_while_samples_are_landing() -> None:
    """Every series in one read must come from the same push.

    Each push writes the same generation number to every series, so a read that
    straddled two of them would come back with two different values — which is
    exactly how an aggregate stops being the mean of its rows.
    """
    keys = [f"s{i}" for i in range(24)]
    store = HistoryStore(100)
    store.ensure_many(keys)
    store.push(dict.fromkeys(keys, 0.0), at=0.0)

    stop = threading.Event()

    def writer() -> None:
        generation = 1
        while not stop.is_set():
            store.push(dict.fromkeys(keys, float(generation)), at=float(generation))
            generation += 1

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        for _ in range(2000):
            # One call, then use its result: calling read() per key would be the
            # very thing this guards against, and does straddle a push in practice.
            reading = store.read(keys)
            values = {reading.of(key).latest for key in keys}
            assert len(values) == 1, f"read straddled a push: {sorted(values)}"
    finally:
        stop.set()
        thread.join(timeout=2.0)


def test_clear_resets_values_and_markers() -> None:
    store = HistoryStore(10)
    store.push({"a": 5.0}, at=1.0)
    store.mark(" 1.0 ")
    store.clear()
    assert store.latest("a") == 0.0
    assert store.marker_state().count == 0
    assert store.marker_state().markers == ()

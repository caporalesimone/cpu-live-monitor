# Sample history: what is kept, and why

Measured on a 12th Gen Intel Core i7-12800H (14C/20T, P + E) with
`HISTORY_CAPACITY = 400`. A second column gives the same figures for a 4-core
ARM board, because the footprint scales with the thread count and that is the
case that matters on constrained hardware.

## The shape of the store

`HistoryStore` is a dictionary of named `TimeSeries`. Each series is an
independent pair of ring buffers — one for values, one for timestamps — capped at
the same capacity:

```
HistoryStore
├── "cpu:total"        deque(maxlen=400) values + deque(maxlen=400) stamps
├── "cpu:lp:0" … :19   one series per logical processor
├── "cpu:core:0" … :13 one series per physical core
├── "cpu:class:P/E"    one series per performance class
├── "cpu:grp2:P:0" …   one series per folded group, per bucket size
├── "mem:used"
└── "mem:backing"
```

**64 series on the i7**, registered up front by the collectors — **21 on the
4-core ARM board**:

| group                 | i7 (14C/20T) | ARM (4C/4T) | why it exists                    |
| --------------------- | -----------: | ----------: | -------------------------------- |
| total                 |            1 |           1 | the TOTAL row                    |
| per logical processor |           20 |           4 | the per-CPU rows                 |
| per physical core     |           14 |           4 | the per-core rows (group size 1) |
| per performance class |            2 |           1 | the P and E summary rows         |
| per folded group      |           25 |           9 | every bucket size the layout may choose |
| memory                |            2 |           2 | RAM and its backing store (swap) |

The folded series are the sum over bucket sizes 2, 3, 4, 6, 8, 12 and 16 (on the
i7: 7 + 5 + 4 + 3 + 2 + 2 + 2). They are all maintained **at all times**, which is
a deliberate trade: resizing the window never finds a row without history, at the
cost of those extra series. Group size 1 is not stored separately because it is
identical to the per-core series.

## How much memory that is

At capacity the store holds **64 × 400 = 25 600 values** on the i7 and the same
number of timestamp slots. Measured heap growth while filling it, with
`tracemalloc`:

```
heap growth  : 1005 KiB   (~1 MB)
per slot     : 20.1 bytes (value + stamp, amortised)
```

On the 4-core board the same arithmetic gives **21 × 400 = 8 400 values**, about
**330 KiB** — worth putting in perspective: the process there measures ~19 MB
resident, so the history is under 2% of it and the rest is the CPython
interpreter itself.

Roughly: 25 600 float objects for the values (~800 KiB), 51 200 deque pointer
slots (~400 KiB), and only **400 float objects for the timestamps** — one per
push, shared by reference across all 64 series, which is why the time base costs
almost nothing.

The footprint is flat: once each ring is full, every push evicts one sample, so
memory does not grow with uptime. It scales with the thread count — roughly 1 MB
per 20 threads, so a 4-thread board costs ~0.3 MB and a 64-thread machine lands
near 3 MB.

## How long a window that is

Capacity is in *samples*, so the wall-clock window depends on the interval:

| interval | window at capacity |
| -------: | -----------------: |
|     0.5s |  200s  =  3.3 min  |
|     1.0s |  400s  =  6.7 min  |
|     2.0s |  800s  = 13.3 min  |
|     5.0s | 2000s  = 33.3 min  |
|    10.0s | 4000s  = 66.7 min  |

`HISTORY_CAPACITY` equals `MAX_HISTORY`, the widest trend the layout will ever
draw (400 cells). That is not a coincidence: the buffer is sized so that even a
full-width terminal is never short of data, and never holds more than it could
show.

## The rules the store follows

**Eviction is by count, not by age.** `deque(maxlen=capacity)` drops the oldest
sample on every push. There is no timer and no compaction.

**Every sample is timestamped.** The displayed duration is therefore *measured*,
never inferred from the interval. `span_for_width(cells)` runs from the start of
the oldest visible bucket — one interval before its stamp — because a sample
stamped at *t* reports activity over the interval that *ends* at *t*. Measuring
stamp-to-stamp would under-report by exactly one cell.

**A cadence change keeps the data and marks the seam.** Pressing F2/F3 does not
clear anything: spans stay correct because they come from timestamps. What the
eye cannot infer is *where* the cells stop being equally spaced, so a marker is
pinned to the next sample and drawn as a seam with the new interval spelled out.
Markers live in their own `deque(maxlen=32)` and are pruned when the sample they
annotate falls out of the window. A burst of key presses drops markers that would
collide, so labels never overlap.

**Markers cost cells, not samples.** A marker takes columns of its own and
pushes older cells left; no sample is ever painted over. The renderer asks for
exactly `trend.samples` values, which is the window width minus the marker cells.

**Threading.** One lock guards the whole store. The sampler thread pushes; the
render loop reads `latest`, `tail`, `span` and `marker_state`. `marker_state()`
returns the sequence number, the retained count and the markers as one atomic
snapshot, so the trend layout can never mix counters from different instants.

## Groundwork that is present but unused

`TimeSeries.window_seconds` and `_resample()` implement a fixed-duration mode —
"always show the last 10 minutes regardless of interval", by bucketing the window
into the requested number of cells and averaging each bucket. Nothing sets it
today; every series runs in one-cell-per-sample mode. It is kept because the
renderer never needs to know which mode a series is in, so switching is a
one-line change at construction.

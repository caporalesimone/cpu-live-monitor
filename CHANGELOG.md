# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-08-18

Delivery, not the app: how a build reaches a device. Nothing the monitor draws
has changed.

### Added

- **Release pipeline** (`.github/workflows/release.yml`). Pushing a `v<x.y.z>`
  tag runs the same gates as `tools/deploy.bat`, builds the archive and opens a
  **draft** release with it attached, alongside a `SHA256SUMS.txt`. Publishing
  stays a human click. The tag must match `[project].version` or the run stops
  before building anything, so a release can never contain a different version
  from the one it claims.
- **Release notes come from this file.** `tools/release_notes.py` prints the
  section matching the version being released, and the workflow passes it to
  `gh release create --notes-file`. A missing or empty section fails the run.
- **Continuous integration** (`.github/workflows/ci.yml`): ruff, ruff format,
  mypy and pytest on every push to `main` and every pull request, on Linux and
  Windows both.
- **`--print-output`** on `tools/build_zipapp.py`, which reports where the
  archive will go without building it. The naming rule is stated once, and the
  batch file, the workflow and the tests all ask rather than assume.

### Changed

- **The archive is named after its version**: `dist/cpumon-1.0.1.pyz` rather
  than `dist/cpumon.pyz`. A file copied onto a device can now be identified
  without being run, and two of them can sit side by side.

## [1.0.0] - 2026-08-17

First release.

### Added

- **Live CPU monitor** for the terminal: one row per logical processor, with the
  busy share since the previous sample, a gauge and a trend of the recent past.
- **Hybrid awareness**: performance, efficiency and low-power cores are detected
  and labelled (`P`, `E`, `LPE`, plus `PHT`/`EHT`/`LPEH` for SMT siblings), and
  each class gets its own summary row.
- **Responsive layout.** Shrinking the window folds SMT siblings, then groups
  cores into buckets, then collapses to per-class rows, then to totals alone.
  Narrowing drops the history column, then the gauge, then the type column. A
  window too small for anything readable says so instead of drawing rubbish.
- **Measured time base.** Every sample is timestamped, so the axis states the
  duration the cells actually cover rather than inferring it from the interval.
  Changing the cadence keeps the history and marks the seam on the trend.
- **Memory rows**: physical memory always, plus the platform's backing store
  (swap) on Linux when swap is configured.
- **Adjustable cadence** from 0.5 s to 10 s, with `F2`/`F3` at runtime or
  `--interval` at startup.
- **Scrollable help page** on `F1`, documenting the keys, the columns, the core
  classes, the colour thresholds and the layout rules.
- **Windows backend**: `GetLogicalProcessorInformationEx` for the topology,
  `NtQuerySystemInformation` for per-processor times, `GlobalMemoryStatusEx` for
  memory, `msvcrt` for keys.
- **Linux backend**: sysfs for the topology (`core_cpus_list` /
  `thread_siblings_list` first, so a non-SMT ARM part is never reported as SMT
  pairs), `/proc/stat` for times, `/proc/meminfo` for memory, termios for keys.
  Falls back through `cpu_capacity` and the device-tree model name where
  `/proc/cpuinfo` has none.
- **CLI**: `--interval`, `--selftest COLS ROWS` (one frame at a given size, for
  diffing and bug reports), `--probe` (backend diagnostics), `--version`.
- **Deployment as a single file.** `tools/build_zipapp.py` builds
  `dist/cpumon.pyz`, about 65 KiB, which runs on any machine with a suitable
  CPython — no poetry, no pip, no install step. The version is read from
  pyproject.toml at build time and baked into the archive; nothing reads
  pyproject.toml at runtime.
- **`tools/deploy.bat`**: runs ruff, ruff format, mypy and pytest, and builds the
  archive only if all four pass.
- **`tools/measure_cpu.py`**: what a process costs, from `/proc` alone, with an
  optional per-thread breakdown — for checking the monitor's own overhead on a
  constrained board.
- **`tools/make_screenshot.py`**: renders a real frame to SVG, so the image in
  the README can be regenerated whenever the look changes.

### Notes on the design

- Standard library only, on Windows and Linux, Python 3.12.
- Layered so that each layer knows only the one below it, with the boundaries
  enforced by `tests/test_architecture.py` rather than merely documented: the
  domain never learns about screens, the UI model never learns about a renderer,
  and glyphs, colours and escape sequences exist in exactly one place. See
  `docs/architecture.md`.
- A frame is produced as plan → build → render, which is what keeps data
  collection, rendering and key handling independent of each other.
- `q` or `Ctrl-C` quits. On the help page `q` closes the page instead, going back
  one level as a pager does.

[Unreleased]: https://github.com/caporalesimone/cpu-live-monitor/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/caporalesimone/cpu-live-monitor/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/caporalesimone/cpu-live-monitor/releases/tag/v1.0.0

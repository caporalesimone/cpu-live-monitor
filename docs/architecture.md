# Architecture review

A structural review of the package, and the state of every abstraction it
claims. The rules described here are executable: `tests/test_architecture.py`
parses every module and fails the build on a violation, so this document cannot
quietly drift from the code.

## Layers

```
cli/          argument parsing, diagnostics, the process entry point
  ↓
runtime/      threads, key bindings, the main loop
  ↓
ui/           frame model, builder, help content, Renderer interface
  ↓  ui/renders/cli/    the ANSI terminal implementation
core/         model, topology, history, collectors      (platform-neutral)
backend/      base.py + windows.py + linux.py           (everything OS specific)
settings.py app_info.py version.py                      (shared leaves)
```

Dependencies point one way only. The enforced table lives in
`tests/test_architecture.py::LAYERS`.

## The three seams that matter

**1. Collection ⟂ presentation.** A frame is produced in three steps:

```python
plan  = renderer.plan(viewport, state, memory, markers)   # what fits, what data that needs
model = session.build(plan.request)                       # values, read from the history store
frame = renderer.render(plan, model)                      # the drawing
```

`FrameRequest` names series and a sample count; `FrameModel` carries numbers,
row *kinds* and *metrics*. Neither mentions a column, a colour or a glyph. The
builder is the only reader of `HistoryStore` on behalf of the display, and the
renderer never touches the store.

**2. The renderer is an interface.** `runtime/app.py` depends on
`ui.renderer.Renderer` and the registry in `ui/renders/`, never on the CLI
implementation. Even the cursor-hiding and screen-restoring sequences are behind
`begin()` / `end()`, so the loop emits no escape sequences of its own. Adding a
presentation is a new subpackage under `ui/renders/` plus one registry entry.

**3. Key handling ⟂ everything.** `InputController` is the only writer of
`UiState`. It sees the viewport as a size (`Viewport(cols, rows)`), never as a
layout, and answers one question: does this key change what should be on screen?

## Abstraction audit

| Abstraction | Verdict | Notes |
| --- | --- | --- |
| `PlatformBackend` / `CpuSampler` / `TerminalBackend` | Sound | Three small interfaces, two complete implementations. `create_backend` lives in `backend/__init__.py`, so the folder holds exactly the three files it should: the interfaces and one module per OS. |
| `MetricCollector` | Sound | Declares its series up front and its cadence as `every_n_ticks`. The indirection is unused today (both collectors run every tick) but costs nothing and is the seam for per-metric rates. |
| `SampleSource` / `MemorySource` | Sound | The domain states its own requirements as two structural `Protocol`s — one method each — instead of importing the platform layer to learn them. A real `CpuSampler` satisfies them without being named, which is why `core/` imports nothing outside itself and stays reusable on its own. |
| `Renderer` / `RenderPlan` | Sound | `plan()` returns an opaque plan whose only public surface is `.request`. `partial_update()`, `begin()` and `end()` have base defaults, so a minimal renderer implements two methods. |
| `FrameModel` / `RowKind` / `MetricKind` | Sound | Presentation is *derived* from kind rather than dictated by the data layer: aggregates get bold, `PROCESSOR`/`GROUP` details get a core-class colour, only `TOTAL` spells the cadence marker out. Had the model carried an `emphasise` flag or a palette object, those would be styling decisions taken a layer too low. |
| `HelpContent` | Sound | Content is semantic lines; `renders/cli/helpstyle.py` styles them 1:1, which is what keeps the scroll arithmetic renderer-independent. The credit line carries author and year, not the `·` and `©`. |
| `LayoutSolver` / `Geometry` | Sound, deliberately CLI | Character-cell geometry belongs to the terminal renderer, so it lives under `renders/cli/`. A GUI renderer would solve its own layout. |
| `RowPlanner` | Sound | Row *selection* is a layout decision, so the renderer owns it; the rows it emits are identities, not appearances. |
| `HistoryStore` / `TimeSeries` | Sound | See `docs/history.md`. `TrendPlan` lives in the renderer rather than the store, because mapping samples onto cells is drawing, not storage. |
| `MonitorSession` | Sound | The assembly point shared by the app and the diagnostics. It wires the pieces and answers requests; it solves no geometry and builds no views. |
| Topology detection | Sound, with a stated degradation chain | Cores are grouped from `topology/core_cpus_list` (or `thread_siblings_list`), the one source that answers "who shares a physical core" on every architecture. `(physical_package_id, core_id)` is only a fallback, requires both ids, and is vetoed when `smt/active` reports SMT off — because a `core_id` is unique only within a package, and assuming otherwise reports a 4-core ARM part as two SMT pairs. Last resort: one core per logical CPU, wrong in a way that misleads nobody. |
| Injectable platform paths | Sound | `LinuxBackend` takes its sysfs and procfs roots as arguments and imports `termios`/`tty` lazily, so the whole platform layer is unit-tested against fake trees on any host. A backend that can only be tested on its own hardware is a backend that is not tested. |
| `TimeSeries.window_seconds` | Dormant | A documented, unused fixed-duration mode. Kept: it costs one branch and the renderer cannot tell the difference. |

## The rules the tests enforce

`tests/test_architecture.py` asserts each of these by parsing every module:

1. **`core` never imports `backend`, `ui`, `runtime` or `cli`.** The domain is the
   reusable half of the program; the structural protocols above are what let it
   stay that way while still driving a platform.
2. **`runtime` never imports a concrete renderer.** The loop talks to
   `ui.renderer.Renderer` and the registry only — which is why the cursor-hiding
   and screen-restoring sequences sit behind `begin()` / `end()` instead of in the
   loop.
3. **Nothing above `ui/renders/` reaches into it.** `ui/builder.py` imports only
   `core` and `ui.model`: no `Geometry`, no column widths, no palettes.
4. **Glyphs, colours and escape sequences exist in one place.** No module outside
   `ui/renders/` may import `glyphs`, `theme`, `ansi`, `widgets`, `palette`,
   `trend` or `helpstyle`.
5. **No two modules import each other** (package/submodule pairs excepted).

One exception is deliberate and asserted as such: `cli/diagnostics.py` may name
the CLI renderer and its escape sequences, because `--selftest` exists to dump a
terminal frame into a log.

## Remaining observations

- **`PlatformBackend` is a fat interface.** Five unrelated methods (topology,
  sampler, terminal, uptime, memory). The collectors no longer take it, so the
  only holder is `MonitorSession`, which uses all five. Splitting it further
  would add types without removing coupling. Left as is.
- **`Geometry` is a wide value object** (10 fields). It is a solver *result*
  consumed in one place; a narrower type per consumer would cost more than it
  saves.
- **Two versions of "how many rows"**: `LayoutSolver` computes `body_rows`, and
  `RowPlanner` then produces exactly that many. They agree because both derive
  from `RowMode` and `group_size`, and a test asserts the planned series all
  exist, but the arithmetic is stated twice.
- **`settings.py` mixes concerns** — sampling bounds and history capacity. Both
  are single-value knobs read by three layers; splitting them would need two
  modules to avoid one import.
- **The renderer registry is static** (`{"cli": ...}`). No entry-point discovery
  and no plugin loading: with one implementation, a dict is the honest amount of
  machinery.

## Evidence

- 403 tests, `ruff` + `ruff format` + `mypy --strict` clean on `src` and `tools`.
- Frames are **byte-for-byte identical** to the single-file implementation this
  package grew out of (preserved on the `singlefile` branch), verified over four
  synthetic topologies, ~17 000 geometries and every screen variant — bar the two
  deliberate changes, the quit key and the version string.
- The platform layer is covered against fake sysfs/procfs trees, including a
  4-core ARM part with no SMT, an x86 SMT part, a hybrid Intel part, and kernels
  that report less than the full picture.
- `tools/deploy.bat` runs all of the above before it will build
  `dist/cpumon-<version>.pyz`, and so do CI and the release workflow.

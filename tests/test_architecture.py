"""The layering, enforced rather than documented.

Every module is parsed and its imports checked against the rules below. This is
what keeps the abstractions from eroding: a shortcut that would couple the data
layer to a renderer, or the UI model to a terminal, fails here rather than in a
review six months later.

Read the table as "may import": a layer may use anything to its right.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = "cpumon"

# Layers, innermost first. A module may import its own layer and any layer
# listed after it, and nothing else inside the package.
LAYERS: dict[str, tuple[str, ...]] = {
    # the shared leaves: no dependencies of their own
    "cpumon": ("cpumon.version",),  # the package __init__ exports the version
    "cpumon.__main__": ("cpumon.cli",),
    "cpumon.settings": (),
    "cpumon.app_info": ("cpumon.version",),
    "cpumon.version": ("cpumon",),  # the baked module it looks for at runtime
    # the domain: platform-neutral, knows nothing about screens or platforms
    "cpumon.core": ("cpumon.settings",),
    # the platforms: implement the domain's view of a machine
    "cpumon.backend": ("cpumon.core", "cpumon.settings"),
    # the presentation model: no characters, no colours, no renderers
    "cpumon.ui": (),  # the package __init__ is documentation only
    "cpumon.ui.model": (),
    "cpumon.ui.state": (),
    "cpumon.ui.help": ("cpumon.ui.model", "cpumon.settings", "cpumon.app_info"),
    "cpumon.ui.builder": ("cpumon.ui.model", "cpumon.ui.state", "cpumon.core"),
    "cpumon.ui.renderer": (
        "cpumon.ui.model",
        "cpumon.ui.state",
        "cpumon.core",
    ),
    # the render backends: everything visual, and the only place that may be
    "cpumon.ui.renders": (
        "cpumon.ui",
        "cpumon.core",
        "cpumon.settings",
        "cpumon.app_info",
    ),
    # the runtime: drives the above, depends on interfaces
    "cpumon.runtime": (
        "cpumon.ui",
        "cpumon.core",
        "cpumon.backend",
        "cpumon.settings",
    ),
    # the entry point: allowed to know everyone
    "cpumon.cli": (
        "cpumon.runtime",
        "cpumon.ui",
        "cpumon.core",
        "cpumon.backend",
        "cpumon.settings",
        "cpumon.app_info",
        "cpumon.version",
    ),
}


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def modules() -> Iterator[tuple[str, Path]]:
    for path in sorted(SRC.rglob("*.py")):
        yield module_name(path), path


def imports_of(path: Path) -> set[str]:
    """Every cpumon module this file imports, at any depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.startswith(PACKAGE))
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(PACKAGE):
            found.add(str(node.module))
    return found


def layer_of(module: str) -> str:
    """The most specific rule that applies to *module*."""
    candidates = [key for key in LAYERS if module == key or module.startswith(key + ".")]
    if not candidates:
        pytest.fail(f"no layering rule covers {module}")
    return max(candidates, key=len)


def allowed(layer: str) -> tuple[str, ...]:
    return (layer, *LAYERS[layer])


ALL_MODULES = list(modules())


def test_every_module_is_covered_by_a_rule() -> None:
    for module, _path in ALL_MODULES:
        assert layer_of(module)


@pytest.mark.parametrize(("module", "path"), ALL_MODULES, ids=lambda v: str(v))
def test_module_imports_stay_inside_its_layer(module: str, path: Path) -> None:
    layer = layer_of(module)
    permitted = allowed(layer)
    for imported in sorted(imports_of(path)):
        assert imported.startswith(permitted), (
            f"{module} (layer {layer}) imports {imported}, which is outside {permitted}"
        )


def test_the_domain_never_learns_about_screens() -> None:
    """core/ is the reusable half of the program; keep it that way."""
    for module, path in ALL_MODULES:
        if not module.startswith("cpumon.core"):
            continue
        for imported in imports_of(path):
            assert not imported.startswith(("cpumon.ui", "cpumon.runtime", "cpumon.cli"))


def test_the_ui_model_never_learns_about_a_renderer() -> None:
    """The point of the split: a second renderer changes nothing above it."""
    for module, path in ALL_MODULES:
        if not module.startswith("cpumon.ui") or module.startswith("cpumon.ui.renders"):
            continue
        for imported in imports_of(path):
            assert not imported.startswith("cpumon.ui.renders"), (
                f"{module} reaches into a render backend"
            )


# `--selftest` exists to dump a terminal frame into a log, so it is allowed to
# name the terminal renderer and its escape sequences. It is the only module
# outside the render backends that may.
_VISUAL_EXCEPTIONS = frozenset({"cpumon.cli.diagnostics"})


def test_only_the_render_backends_draw() -> None:
    """Glyphs, colours and escape sequences live in exactly one place."""
    visual = ("glyphs", "theme", "ansi", "widgets", "palette", "trend", "helpstyle")
    for module, path in ALL_MODULES:
        if module.startswith("cpumon.ui.renders") or module in _VISUAL_EXCEPTIONS:
            continue
        for imported in imports_of(path):
            leaf = imported.rsplit(".", 1)[-1]
            assert leaf not in visual, f"{module} imports the visual module {leaf}"


def test_the_runtime_does_not_depend_on_a_concrete_renderer() -> None:
    """The loop talks to the Renderer interface and the registry, nothing else."""
    for module, path in ALL_MODULES:
        if not module.startswith("cpumon.runtime"):
            continue
        for imported in imports_of(path):
            assert not imported.startswith("cpumon.ui.renders.cli"), (
                f"{module} imports {imported}; use cpumon.ui.renderer or the registry"
            )


def _related(a: str, b: str) -> bool:
    """True when one module is the other's package, which is not a cycle."""
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def test_no_two_modules_import_each_other() -> None:
    """A cheap cycle check. Package/submodule pairs are exempt: a package
    __init__ exporting its own submodule is normal, not a cycle.
    """
    graph = {module: imports_of(path) for module, path in ALL_MODULES}
    for module, imported in graph.items():
        for other in imported:
            if _related(module, other):
                continue
            assert module not in graph.get(other, set()), f"cycle: {module} <-> {other}"

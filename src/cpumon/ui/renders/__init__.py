"""Available render backends.

One subpackage per presentation. ``cli`` is the character-cell terminal one and
the only implementation today; the point of the split is that a second one is an
addition here rather than an edit anywhere else.
"""

from __future__ import annotations

from collections.abc import Callable

from cpumon.core.model import Topology
from cpumon.ui.help import HelpContent
from cpumon.ui.renderer import Renderer

DEFAULT_RENDERER = "cli"


def _create_cli(
    topology: Topology, *, has_backing: bool, help_content: HelpContent | None
) -> Renderer:
    from cpumon.ui.renders.cli import CliRenderer

    return CliRenderer(topology, has_backing=has_backing, help_content=help_content)


_FACTORIES: dict[str, Callable[..., Renderer]] = {"cli": _create_cli}


def available_renderers() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def create_renderer(
    name: str = DEFAULT_RENDERER,
    *,
    topology: Topology,
    has_backing: bool = False,
    help_content: HelpContent | None = None,
) -> Renderer:
    """Build the named renderer for *topology*.

    ``help_content`` is passed in rather than created here so the controller can
    scroll exactly the document the renderer draws.
    """
    try:
        factory = _FACTORIES[name]
    except KeyError:
        raise ValueError(
            f"unknown renderer {name!r}; available: {', '.join(available_renderers())}"
        ) from None
    return factory(topology, has_backing=has_backing, help_content=help_content)

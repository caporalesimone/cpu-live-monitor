"""The renderer registry, and that the CLI one honours the interface."""

from __future__ import annotations

import pytest

from cpumon.core.model import Topology
from cpumon.ui.help import HelpContent
from cpumon.ui.renderer import Renderer, RenderPlan
from cpumon.ui.renders import (
    DEFAULT_RENDERER,
    available_renderers,
    create_renderer,
)
from cpumon.ui.renders.cli import CliPlan, CliRenderer


def test_the_default_renderer_is_available() -> None:
    assert DEFAULT_RENDERER in available_renderers()


def test_the_factory_builds_the_cli_renderer(hybrid_topology: Topology) -> None:
    renderer = create_renderer(topology=hybrid_topology)
    assert isinstance(renderer, CliRenderer)
    assert isinstance(renderer, Renderer)


def test_an_unknown_name_is_refused_with_the_alternatives(
    hybrid_topology: Topology,
) -> None:
    with pytest.raises(ValueError, match="unknown renderer 'ascii-art'"):
        create_renderer("ascii-art", topology=hybrid_topology)


def test_the_help_document_is_shared_not_rebuilt(hybrid_topology: Topology) -> None:
    """The controller must scroll exactly the page the renderer draws."""
    content = HelpContent()
    renderer = create_renderer(topology=hybrid_topology, help_content=content)
    assert isinstance(renderer, CliRenderer)
    assert renderer._help is content


def test_the_cli_plan_is_a_render_plan(hybrid_topology: Topology) -> None:
    from cpumon.core.history import MarkerState
    from cpumon.ui.model import Viewport
    from cpumon.ui.state import UiState

    renderer = CliRenderer(hybrid_topology)
    plan = renderer.plan(Viewport(120, 40), UiState(), None, MarkerState(0, 0))
    assert isinstance(plan, CliPlan)
    assert isinstance(plan, RenderPlan)
    assert plan.request is plan._request


def test_a_renderer_with_nothing_to_refresh_says_so() -> None:
    """partial_update is optional: the base class default is 'nothing'."""

    class Silent(Renderer):
        def plan(self, viewport, state, memory, markers):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def render(self, plan, model):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    assert Silent().partial_update() == ""

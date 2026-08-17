"""The contract every render backend implements.

A renderer owns two decisions the rest of the app must not make for it: how
much detail fits in the viewport, and what the result looks like. It states the
first as a :class:`~cpumon.ui.model.FrameRequest` and performs the second in
:meth:`Renderer.render`.

The application talks to this interface only, so adding a second presentation —
a plain-text log, a web page, a GUI — is a new module under
:mod:`cpumon.ui.renders`, not a change to the loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from cpumon.core.history import MarkerState
from cpumon.core.model import MemoryInfo
from cpumon.ui.model import FrameModel, FrameRequest, Viewport
from cpumon.ui.state import UiState


class RenderPlan(ABC):
    """A renderer's decisions for one frame, opaque to everyone else.

    The only part the rest of the app reads is :attr:`request`, which the
    builder needs in order to fetch data.
    """

    @property
    @abstractmethod
    def request(self) -> FrameRequest:
        """Which series this frame needs, and how many samples of each."""


class Renderer(ABC):
    """Turns a frame model into whatever the output device consumes."""

    @abstractmethod
    def plan(
        self,
        viewport: Viewport,
        state: UiState,
        memory: MemoryInfo | None,
        markers: MarkerState,
    ) -> RenderPlan:
        """Decide what this frame will show.

        *markers* describes where the sampling cadence changed, which can cost
        room and therefore change how many samples the frame can hold.
        """

    @abstractmethod
    def render(self, plan: RenderPlan, model: FrameModel) -> str:
        """The frame payload, ready to be written to the output device."""

    def begin(self) -> str:
        """What to write once before the first frame, or "" for nothing.

        A terminal renderer hides the cursor here. Keeping it behind the
        interface is what allows the main loop to emit no escape sequences of
        its own.
        """
        return ""

    def end(self) -> str:
        """What to write once after the last frame, to leave a sane device."""
        return ""

    def partial_update(self) -> str:
        """A cheap repaint of whatever changes on its own, or "" for nothing.

        A terminal renderer uses this for the wall clock, so an idle monitor
        does not redraw the whole screen once a second. Renderers that have
        nothing to offer here keep the default.
        """
        return ""

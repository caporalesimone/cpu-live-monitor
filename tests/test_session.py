"""Session wiring, including the guard that refuses mislabelled data."""

from __future__ import annotations

import pytest

from cpumon.core.errors import PlatformError
from cpumon.core.history import SeriesKey
from cpumon.core.model import CoreClass, MemoryInfo, Topology
from cpumon.core.topology import build_topology
from cpumon.runtime.session import MonitorSession
from cpumon.settings import INTERVAL_MAX, INTERVAL_MIN
from cpumon.ui.model import FrameRequest, RowKind, RowSpec, ScreenKind
from tests.conftest import HYBRID_SPEC, FakeSampler, StubBackend


def make_backend(**kwargs: object) -> StubBackend:
    return StubBackend(**kwargs)  # type: ignore[arg-type]


def test_session_registers_every_series() -> None:
    session = MonitorSession.create(make_backend(), 1.0)
    declared = {key for c in session.collectors for key in c.series_keys()}
    assert SeriesKey.TOTAL in declared
    assert SeriesKey.MEMORY in declared
    for cpu in session.topology.cpus:
        assert SeriesKey.cpu(cpu.index) in declared


@pytest.mark.parametrize(
    ("asked", "expected"),
    [(0.0, INTERVAL_MIN), (1.0, 1.0), (99.0, INTERVAL_MAX), (0.74, 0.7)],
)
def test_interval_is_clamped_at_startup(asked: float, expected: float) -> None:
    session = MonitorSession.create(make_backend(), asked)
    assert session.state.interval == expected


def test_uptime_is_read_on_demand() -> None:
    session = MonitorSession.create(make_backend(), 1.0)
    assert session.state.uptime == 0.0
    session.refresh_uptime()
    assert session.state.uptime == 4242.0


def test_memory_is_primed_at_construction() -> None:
    session = MonitorSession.create(make_backend(), 1.0)
    assert session.memory_info.total > 0
    assert session.has_backing is False


def test_a_platform_with_swap_says_so() -> None:
    swap = MemoryInfo(
        total=100, available=50, backing_kind="swap", backing_total=100, backing_used=1
    )
    session = MonitorSession.create(make_backend(memory=swap), 1.0)
    assert session.has_backing is True


def test_the_session_answers_a_request_without_knowing_the_layout() -> None:
    session = MonitorSession.create(make_backend(), 1.0)
    session.worker(lambda: None).collect_once()
    request = FrameRequest(
        ScreenKind.DASHBOARD,
        rows=(RowSpec(RowKind.TOTAL, "TOTAL", "20T", SeriesKey.TOTAL),),
        sample_count=1,
    )
    model = session.build(request)
    assert model.rows[0].value == pytest.approx(1.0)
    assert model.machine.threads == session.topology.n_cpus


def test_markers_are_exposed_for_the_renderer() -> None:
    session = MonitorSession.create(make_backend(), 1.0)
    session.worker(lambda: None).collect_once()
    session.history.mark(" 0.9 ")
    state = session.markers()
    assert state.count == 1
    assert state.markers[-1][1] == " 0.9 "


def test_worker_pushes_into_the_session_history() -> None:
    session = MonitorSession.create(make_backend(), 1.0)
    session.worker(lambda: None).collect_once()
    assert session.history.latest(SeriesKey.TOTAL) == pytest.approx(1.0)
    assert session.history.latest(SeriesKey.MEMORY) == pytest.approx(50.0)


def test_sampler_count_mismatch_is_refused() -> None:
    topo = build_topology("Stub", HYBRID_SPEC)
    backend = StubBackend(topo, FakeSampler([1.0] * (topo.n_cpus - 1)))
    with pytest.raises(PlatformError, match="refusing to display data"):
        MonitorSession.create(backend, 1.0)


def test_non_contiguous_indices_are_refused() -> None:
    good = build_topology("Stub", [(CoreClass.P, [0, 1])])
    broken = Topology(
        model_name="Stub",
        cpus=tuple(
            type(cpu)(
                index=cpu.index + 5,
                lp_id=cpu.lp_id,
                core_id=cpu.core_id,
                core_class=cpu.core_class,
                smt_index=cpu.smt_index,
            )
            for cpu in good.cpus
        ),
        cores=good.cores,
    )
    backend = StubBackend(broken, FakeSampler([1.0, 2.0]))
    with pytest.raises(PlatformError, match="non-contiguous"):
        MonitorSession.create(backend, 1.0)

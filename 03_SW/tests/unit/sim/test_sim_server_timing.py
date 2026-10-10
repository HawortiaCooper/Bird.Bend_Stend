"""OBS-P3-01 (SW_perf_report_devpc): the out-of-process simulator requests 1 ms timer resolution while it runs, so
its 1 ms board loop paces DATA at 12.5 ms instead of 15.6 ms bursts; it releases it only if it switched it on.

Verifies: SYS-008, NFR-001
"""
from __future__ import annotations

import sys

import pytest

from bend_stand.core import timing
from bend_stand.io.sim.server import SimServer


@pytest.mark.req("SYS-008", "NFR-001")
@pytest.mark.skipif(sys.platform != "win32", reason="winmm timer resolution is Windows-only")
def test_sim_server_owns_the_timer_resolution_while_running() -> None:
    was = timing.is_active()
    srv = SimServer(0, 0)
    srv.start()
    try:
        assert timing.is_active()
        assert srv._timing_owned is (not was)  # noqa: SLF001
    finally:
        srv.stop()
    assert timing.is_active() is was

"""Validator E - DEF-M3-01: stale sniffed-stop hold (found by the HIL twin dry run, 2026-10-05).

FW_design §5.9.3: the 1 kHz stop sniffer records a hold {TYPE, SEQ}; the in-order dispatch of the same frame
releases it (hold_on_dispatch). When the main-loop dispatcher handles the STOP / HALT / PAUSE frame BEFORE the next
1 kHz tick scans it, hold_on_dispatch() finds no pending hold, and the sniffer then sets a hold for a frame that was
already dispatched. It is released only by the SNIFF_HOLD_MAX_MS (20 ms) timeout, which "resolves on the safe side":
a motion command accepted in that window is discarded with STOPPED(<old cause>) + MOVE_DONE STOPPED.
Effect: a MOVE_ABS / JOG / HOME sent < 20 ms after a confirmed stop is silently stopped (SW: spurious stop banner,
HOME_FAILED ABORTED). Expected (ICD §5.4, FW_design §5.9.3): a command from a frame received AFTER the stop frame
starts normally.

Verifies: FW-MOT-007, SAF-FW-002 (sniffed-stop hold semantics, FW_design §5.9.3)
TC: TC-SAF-FW-002-03 (extension: command after the stop frame)
"""
from __future__ import annotations

import pytest

import ref_codec as rc
import vhelp_m2 as m
from vhelp import V

MD = {n: i for i, n in enumerate(rc.MOVE_DONE_REASON)}


@pytest.mark.xfail(strict=True, reason="DEF-M3-01: stale sniffed-stop hold discards a motion start ≤ 20 ms after a "
                                       "stop frame that was dispatched before the sniffer scanned it")
@pytest.mark.parametrize("stop", ["STOP0", "HALT", "PAUSE"])
def test_motion_after_dispatched_stop_not_discarded(v: V, stop):
    m.need(v, "MOTION", "HOMING")
    m.ready(v, x_um=50_000)
    n0 = m.n_events(v)
    assert m.jog(v, 10_000)["status"] == "OK"
    m.run(v, 300)
    if stop == "STOP0":
        v.ok("STOP", {"mode": 0})
    else:
        v.ok(stop)
    m.wait_event(v, "MOVE_DONE", n0, 3000)
    if stop == "HALT":
        v.ok("HALT_CLEAR")
    if stop == "PAUSE":
        v.ok("RESUME")
    n1 = m.n_events(v)
    v.ok("MOVE_ABS", {"target_um": 50_000, "v_um_s": 20_000, "a_um_s2": 0})   # well inside 20 ms of the stop
    md = m.wait_event(v, "MOVE_DONE", n1, 5000)
    stopped = m.events_since(v, n1, "STOPPED")
    assert md["arg"] == MD["TARGET"] and not stopped, (md, stopped)

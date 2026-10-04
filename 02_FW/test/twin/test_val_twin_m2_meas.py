"""Validator E - DIAG_MEAS (ICD v0.6 Appendix C, D-40 c) through the twin's HW_MEAS model (`Twin(hw_meas=True)`,
REQ-C-M2-08): the core's DIAG_MEAS path, MEAS_STATE gating, and a dry run of the HG evidence chain (independent
counter vs pos_steps, IWDG hang read back from .noinit). The twin model is the Integrator's; the target values
come at the HW gate (HG-09, HG-14, HG-29). Not M2 acceptance of the target HAL (meas_f4.c is target-only).

Verifies: SYS-009 (measurement path, dry run), IF-012 (DIAG_MEAS), SAF-FW-004 / SAF-FW-019 (evidence chain)
TC: TC-IF-012-01 (DIAG_MEAS part), TC-SYS-009-01 (dry run of HG-09 / HG-14 / HG-29 c-d)
"""
from __future__ import annotations

import pytest

import ref_codec as rc
import vhelp_m2 as m
from twin import Twin
from vhelp import V

OP = {n: i for i, n in enumerate(rc.MEAS_OP)}


@pytest.fixture
def vm(twin_exe, tmp_path):
    t = Twin("lockstep", exe=twin_exe, run_dir=tmp_path / "meas", hw_meas=True)
    yield V(t)
    t.close()


def meas(v, op: str, sel: int = 0, a: int = 0, b: int = 0) -> dict:
    return v.cmd("DIAG_MEAS", {"op": OP[op], "sel": sel, "a": a, "b": b})


def test_info_and_feature_bit(vm):
    m.need(vm, "MOTION", "HW_MEAS")
    r = meas(vm, "INFO")
    assert r["status"] == "OK" and r["w"][1] == 180_000_000 and r["w"][3] == 1_000_000, r
    assert meas(vm, "INFO", sel=1)["status"] == "E_RANGE"


def test_meas_state_gating(vm):
    m.need(vm, "MOTION", "HOMING", "HW_MEAS")
    r = meas(vm, "HANG", sel=0, a=100)                            # not moving
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("MEAS_STATE")), r
    m.ready(vm, x_um=20_000)
    r = meas(vm, "STATIC_LEVEL", sel=0, a=1)                      # enabled: refused
    assert r["status"] == "E_STATE" and r["detail"] & (1 << rc.BLOCK.index("MEAS_STATE")), r
    vm.ok("DISABLE")
    assert meas(vm, "STATIC_LEVEL", sel=0, a=1)["status"] == "OK"


def test_counter_equals_pos_steps(vm):
    """Dry run of HG-09: the independent counter (MT-2) vs Δpos_steps per move."""
    m.need(vm, "MOTION", "HOMING", "HW_MEAS")
    m.ready(vm, x_um=20_000)
    for tgt in (35_000, 21_000, 80_000):
        assert meas(vm, "COUNTER", sel=1)["status"] == "OK"       # read + reset
        s0 = vm.status()["pos_steps"]
        m.move_abs(vm, tgt, 10_000)
        c = meas(vm, "COUNTER")["w"][0]
        assert c == abs(vm.status()["pos_steps"] - s0), (tgt, c)


def test_hang_noinit_readback(vm):
    """Dry run of HG-14 on the ICD v0.7.1 NOINIT record (DEF-M2-02 fix): HANG while moving -> IWDG reset; after the
    reboot w4 = 1 and the previous boot's newest PUL stamp w5 and last heartbeat w6 give "PUL stops <= 100 ms after
    the hang start (w7)" and the time to the reset; w8 counts the boot. A pin reset keeps the block; sel 1 clears."""
    m.need(vm, "MOTION", "HOMING", "HW_MEAS")
    m.ready(vm, x_um=20_000)
    assert meas(vm, "NOINIT", sel=1)["status"] == "OK"            # HG-14 step: clear the record before a trial
    w0 = meas(vm, "NOINIT")["w"]
    assert w0[0] == 0x4D454153 and w0[8] == 0, w0
    m.move_abs(vm, 200_000, 10_000, wait=False)
    m.run(vm, 200)
    assert meas(vm, "HANG", sel=0, a=0)["status"] == "OK"
    vm.advance(300)
    assert vm.status()["reset_cause"] == "IWDG"
    w = meas(vm, "NOINIT")["w"]
    assert w[0] == 0x4D454153 and w[4] == 1 and w[8] == 1, w
    prev_pul, prev_hb, hang = w[5], w[6], w[7]
    assert prev_pul != 0 and ((prev_pul - hang) & 0xFFFFFFFF) <= 100_000, (prev_pul, hang)
    t_reset = (prev_hb - hang) & 0xFFFFFFFF
    assert t_reset <= 100_000, (prev_hb, hang)                    # IWDG <= 90 ms + heartbeat period
    assert w[1] == 0                                              # this boot: no PUL yet
    vm.tw.act("reset", cause="pin")
    vm.advance(60)
    w2 = meas(vm, "NOINIT", sel=1)["w"]
    assert w2[4] == 1 and w2[8] == 2 and w2[7] == hang, w2        # survives a pin reset, boot counted
    w3 = meas(vm, "NOINIT")["w"]
    assert w3[0] == 0x4D454153 and w3[4:9] == [0, 0, 0, 0, 0], w3  # cleared by sel 1
    print(f"VALOBS HANG dry run: last PUL {(prev_pul - hang) & 0xFFFFFFFF} µs, reset {t_reset} µs after the hang")


def test_dwt_sections_not_in_meas_build(vm):
    """op 9 in the twin / HW_MEAS (non-DWT) model: w0 valid = 0 for every section 0…22 (DWT exists only in the
    HW_MEAS_DWT target image, OI-FW-37; its numbers are HG-18 evidence)."""
    m.need(vm, "MOTION", "HW_MEAS")
    for sec in (0, 11, 22, 23):
        r = meas(vm, "DWT", sel=0, a=sec)
        assert r["status"] == "OK" and r["w"][0] == 0, (sec, r)

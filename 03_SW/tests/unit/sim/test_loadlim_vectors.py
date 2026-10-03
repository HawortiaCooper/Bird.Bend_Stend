"""Simulator FW load limit (ICD v0.6 §5.5, D-12, D-40 d) against the Integrator's ``loadlim_vectors.json``, through
the pure model and through the SimBoard sample path (raw script, FAULT_CLEAR, SET_PARAM); DIAG_MEAS (ICD v0.6
App. C) in the simulator = a release build: E_INTERNAL NOT_IN_BUILD after the LEN check for every hw_meas vector.

Verifies: SAF-FW-008, SAF-FW-009, SAF-FW-010, SAF-FW-011, SYS-008, FW-CMD-001
"""
from __future__ import annotations

import pytest

from bbs_support import vectors
from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.io import protocol as P
from bend_stand.io.sim.check import SimCheckState, check
from bend_stand.io.sim.loadlim import LoadLimit
from sim.test_sim_board import Client

LV = vectors("loadlim_vectors.json")
CV = vectors("check_vectors.json")
Cmd = pg.Cmd


@pytest.mark.req("SAF-FW-008", "IF-010")
def test_loadlim_header() -> None:
    assert LV["icd_version"] == pg.ICD_VERSION and int(LV["param_dict_hash"], 16) == pgen.PARAM_DICT_HASH
    assert LV["cases"]


@pytest.mark.req("SAF-FW-008", "SAF-FW-009", "SAF-FW-011")
@pytest.mark.parametrize("c", LV["cases"], ids=lambda c: c["name"])
def test_loadlim_model_replay(c: dict) -> None:
    i = c["init"]
    m = LoadLimit()
    m.config(i["load_raw_min"], i["load_raw_max"], i["trip_samples"], i["regrow"])
    for st in c["steps"]:
        if st["op"] == "sample":
            assert m.sample(st["raw"]) is st["trip"], st
        elif st["op"] == "fault_clear":
            m.fault_clear()
            assert m.ref == st["ref"], st
        else:
            m.config(st["load_raw_min"], st["load_raw_max"], st["trip_samples"], st["regrow"])
        assert m.window is st["regrow_window"], st


@pytest.mark.req("SAF-FW-008", "SAF-FW-011", "SYS-008")
@pytest.mark.parametrize("c", LV["cases"], ids=lambda c: c["name"])
def test_loadlim_simboard_replay(c: dict) -> None:
    """Same steps through the board: samples into the board's sample path (AFE otherwise stalled), FAULT_CLEAR / SET_PARAM on the wire; a trip = a new
    FAULT_SET LOAD_LIMIT event (or the latch staying set)."""
    cl = Client()
    i = c["init"]
    for key, v in (("safety.load_raw_max", 7_151_121), ("safety.load_raw_min", i["load_raw_min"]),
                   ("safety.load_raw_max", i["load_raw_max"]), ("safety.load_trip_samples", i["trip_samples"]),
                   ("safety.load_regrow_raw", i["regrow"])):
        assert cl.cmd(Cmd.SET_PARAM, P.build_set_param(key, v)).ok, key
    cl.run(30)
    cl.ctl.act("afe", stall=True)                            # only the scripted samples below
    for st in c["steps"]:
        if st["op"] == "sample":
            n0 = len(cl.ev("FAULT_SET"))
            with cl.b._lock:  # noqa: SLF001
                cl.b._on_sample(cl.b.now_us(), st["raw"])    # noqa: SLF001  (the HX711 sample path)
            cl.run(1)
            tripped = len(cl.ev("FAULT_SET")) > n0
            if st["trip"]:
                assert tripped or cl.b.faults_mask & pg.Faults.LOAD_LIMIT, st
            else:
                assert not tripped, st
        elif st["op"] == "fault_clear":
            assert cl.cmd(Cmd.FAULT_CLEAR).ok and not cl.b.faults_mask & pg.Faults.LOAD_LIMIT
        else:
            for key, v in (("safety.load_raw_min", st["load_raw_min"]), ("safety.load_raw_max", st["load_raw_max"]),
                           ("safety.load_trip_samples", st["trip_samples"]),
                           ("safety.load_regrow_raw", st["regrow"])):
                assert cl.cmd(Cmd.SET_PARAM, P.build_set_param(key, v)).ok
        assert cl.b.loadlim.window is st["regrow_window"], st


@pytest.mark.req("FW-CMD-001", "SYS-008")
@pytest.mark.parametrize("v", CV.get("hw_meas_vectors", []), ids=lambda v: v["name"])
def test_diag_meas_not_in_build_in_the_simulator(v: dict) -> None:
    """The simulator is a release build (no FEAT_HW_MEAS): every correctly sized DIAG_MEAS → NOT_IN_BUILD."""
    st = SimCheckState.from_vector(CV["state_defaults"], v["state"], CV["state_schema"])
    r = v["request"]
    payload = bytes.fromhex(r["payload_hex"])
    got = check(st, int(r["type"], 16), payload)
    if len(payload) != pg.CMD_REQ_LEN[pg.Cmd.DIAG_MEAS]:
        assert got == ("E_LENGTH", pg.CMD_REQ_LEN[pg.Cmd.DIAG_MEAS])
    else:
        assert got == ("E_INTERNAL", int(pg.InternalDetail.NOT_IN_BUILD))

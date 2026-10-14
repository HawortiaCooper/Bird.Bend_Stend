"""Bring-up assistant — Validator F's review items (SW_code_review v1.6 §8.10): SWD-P3-03 observed values in the
automatic result, SWD-P3-04 another board in a session, SWD-P3-05 CSP of the report, SWD-P3-07 session references
outside the data folders, SWD-P3-08 damaged session file, SWD-P3-09 no false EVENT loss at a FW restart,
OBS-F-BRU-01 simulator ALM follows the driver supply.

Verifies: SW-BRU-001, SW-BRU-002, SW-BRU-003, SW-PLT-003, FW-SW-004
"""
from __future__ import annotations

import copy
import json
import logging
import os
import zipfile
from pathlib import Path

import pytest
from bbs_support import lockstep_backend

from bend_stand.core import bringup
from bend_stand.core import protocol_gen as pg
from bend_stand.core.errors import FileFormatError

from .test_bringup import EXAMPLE, _be, _session_json, _st, _step, _wait_state


def _doc_file(tmp_path: Path, doc: dict) -> str:
    p = tmp_path / "steps_review.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


# ------------------------------------------------------------------------------------------------ SWD-P3-03

@pytest.mark.req("SW-BRU-001")
def test_waiting_text_shows_the_observed_values_and_fail_if_ne_on_info_and_param(tmp_path) -> None:
    doc = copy.deepcopy(EXAMPLE)
    s63 = _step(doc, "6.3")
    s63["auto"]["fail_if"] = [{"src": "info", "key": "build", "op": "ne", "value": "20261010-2135", "label": "build"}]
    s67 = _step(doc, "6.7")
    s67["auto"]["fail_if"] = [{"src": "param", "key": "motion.steps_per_mm", "op": "ne", "value": 801,
                               "label": "motion.steps_per_mm"}]
    be = lockstep_backend(bringup_steps=_doc_file(tmp_path, doc))
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        build = be.device.info.build
        assert _wait_state(be, "6.3") == "FAIL"
        a = bu.step("6.3").auto
        assert a.state == "FAIL" and f"build = {build}" in a.text, a
        bu.select("6.7")
        assert _wait_state(be, "6.7") == "FAIL" and "motion.steps_per_mm = 800" in bu.step("6.7").auto.text
        # waiting: the unmet conditions with their observed values (fixture 6.3 without fail_if)
        be2_doc = copy.deepcopy(EXAMPLE)
        bu.select("6.5")
        h.advance(300)
    finally:
        be.shutdown()
    be = lockstep_backend(bringup_steps=_doc_file(tmp_path, be2_doc))
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start(confirmed=True).ok                            # current = 6.3: FW 0.8.6 expected
        h.advance(500)
        a = bu.step("6.3").auto
        fw = ".".join(map(str, be.device.info.fw_version))
        assert a.state == "WAITING" and "observed:" in a.text and f"FW = {fw}" in a.text, a
        assert f"build = {be.device.info.build}" in a.text
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001")
def test_fail_if_ne_is_not_triggered_before_the_value_is_known() -> None:
    ctx = bringup.EvalContext()
    c = {"src": "info", "key": "build", "op": "ne", "value": "x"}
    assert bringup.eval_condition(c, ctx) == (False, None)
    assert bringup.eval_condition({"src": "param", "key": "motion.steps_per_mm", "op": "ne", "value": 800}, ctx)[0] \
        is False


# ------------------------------------------------------------------------------------------------ SWD-P3-04

@pytest.mark.req("SW-BRU-002", "SW-BRU-003")
def test_another_board_stops_the_session_until_a_new_session_or_the_package(tmp_path, monkeypatch) -> None:
    import bend_stand.io.sim.board as simboard

    be = _be(tmp_path)
    bu = be.bringup
    assert bu.start().ok
    uid1 = bu.status().board_uid
    assert bu.submit("6.9", {"sign": "increases"}) == []
    be.shutdown()
    other = "0123456789ABCDEF01234567"
    monkeypatch.setattr(simboard, "SIM_UID", other)
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        st = bu.status()
        assert st.active and st.board_uid == uid1 and st.board_mismatch and other in st.board_mismatch
        assert "new session for this board" in st.board_mismatch
        assert bu.submit("8.6", {"free": True})[0].code == "BOARD_CHANGED"
        assert bu.mark("13.1", "PASS").codes() == ("BOARD_CHANGED",)
        assert bu.run_action("10.2.3", confirmed=True).codes() == ("BOARD_CHANGED",)
        bu.select("6.5")
        h.advance(1500)
        assert bu.step("6.5").auto.state == "NA" and _st(be, "6.5") == "NOT_DONE"   # no results from this board
        ses = _session_json(be)
        assert [b["uid"] for b in ses["boards_seen"]] == [uid1, other] and ses["board_mismatch"]["connected_uid"] == other
        pkg = h.result(bu.create_package_async(), 30_000)          # packing the old session stays possible
        with zipfile.ZipFile(pkg.path) as zf:
            page = zf.read("report.html").decode("utf-8")
            rec = json.loads(zf.read("bringup.json"))
        assert other in page and uid1 in page and "Board changed" in page
        assert rec["session"]["board_mismatch"]["connected_uid"] == other and "another board" in rec["notes"][0]
        g = bu.start()
        assert g.needs_confirmation and other in g.text()
        assert bu.start(confirmed=True).ok
        st = bu.status()
        assert st.board_uid == other and st.board_mismatch is None and bu.mark("13.1", "PASS").ok
    finally:
        be.shutdown()


# ------------------------------------------------------------------------------------------------ SWD-P3-05

@pytest.mark.req("SW-BRU-003")
def test_report_html_carries_the_csp(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        assert be.bringup.start().ok
        pkg = be.test_hooks.result(be.bringup.create_package_async(), 30_000)
        with zipfile.ZipFile(pkg.path) as zf:
            page = zf.read("report.html").decode("utf-8")
        assert "http-equiv='Content-Security-Policy'" in page and "default-src 'none'" in page
        assert "<script" not in page
    finally:
        be.shutdown()


# ------------------------------------------------------------------------------------------------ SWD-P3-07

@pytest.mark.req("SW-BRU-003")
@pytest.mark.parametrize("how", ["absolute", "dotdot", "root_planted"])
def test_session_references_outside_the_data_folders_are_skipped(tmp_path, caplog, how) -> None:
    be = _be(tmp_path)
    try:
        assert be.bringup.start().ok
        folder = Path(be.bringup.status().folder)
        assert be.bringup.close_session().ok
    finally:
        be.shutdown()
    data = Path(be.data_dir)
    outside = tmp_path / "outside" / "secret"
    outside.mkdir(parents=True)
    (outside / "SECRET.txt").write_text("secret", encoding="utf-8")
    f = folder / "session.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    if how == "absolute":
        d["recordings"].append({"step": "9.6", "folder": str(outside), "root": str(outside.parent)})
    elif how == "dotdot":                                           # looks like it is below the data folder
        rel = os.path.relpath(outside, data)                       # data/../../…/outside/secret
        assert rel.startswith("..")
        d["recordings"].append({"step": "9.6", "folder": str(data) + os.sep + rel, "root": str(data)})
    else:                                                           # a root planted next to a harmless folder
        d["recordings"].append({"step": "9.6", "folder": str(outside), "root": str(tmp_path)})
    f.write_text(json.dumps(d), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="bend_stand.incident"):
        pkg = bringup.build_package(folder, tmp_path / "out", data_dir=data)
    with zipfile.ZipFile(pkg.path) as zf:
        names = zf.namelist()
        rec = json.loads(zf.read("bringup.json"))
    assert not [n for n in names if "SECRET" in n], names
    assert any("outside the data folders" in n for n in rec["notes"])
    assert any("outside the application's data folders" in r.getMessage() for r in caplog.records)
    assert bringup.verify_package(pkg.path) == []


@pytest.mark.req("SW-BRU-003")
def test_recording_roots_come_from_the_application_not_the_session(tmp_path, monkeypatch) -> None:
    data = tmp_path / "data"
    (data / "sessions").mkdir(parents=True)
    (data / "sessions" / "default.bbsession.json").write_text(json.dumps({"recordings_root": str(tmp_path / "r1")}),
                                                              encoding="utf-8")
    (data / "sessions" / "broken.json").write_text("{", encoding="utf-8")
    monkeypatch.setenv("BEND_STAND_RECORDINGS_DIR", str(tmp_path / "r0"))
    assert bringup.app_recording_roots(data) == [tmp_path / "r0", tmp_path / "r1"]


# ------------------------------------------------------------------------------------------------ SWD-P3-08

@pytest.mark.req("SW-BRU-002")
@pytest.mark.parametrize("damage", ["steps_str", "record_list", "auto_str", "recordings_dict", "board_list",
                                    "state_bad", "log_str"])
def test_damaged_member_types_are_session_unreadable(tmp_path, caplog, damage) -> None:
    be = _be(tmp_path)
    bu = be.bringup
    assert bu.start().ok
    sid, folder = bu.status().session_id, Path(bu.status().folder)
    be.shutdown()
    f = folder / "session.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    first = next(iter(d["steps"]))
    {"steps_str": lambda: d.update(steps="garbage"), "record_list": lambda: d["steps"].update({first: [1]}),
     "auto_str": lambda: d["steps"][first].update(auto="x"), "recordings_dict": lambda: d.update(recordings={}),
     "board_list": lambda: d.update(board=[1]), "state_bad": lambda: d["steps"][first].update(state="MAYBE"),
     "log_str": lambda: d.update(log="x")}[damage]()
    f.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(FileFormatError):
        bringup.check_session_doc(d, "x")
    with caplog.at_level(logging.ERROR):
        be = _be(tmp_path)
    try:
        bu = be.bringup
        assert not bu.status().active                                # not resumed, no crash
        assert any("bring-up session not resumed" in r.getMessage() for r in caplog.records)
        g = bu.resume(sid)
        assert g.codes() == ("SESSION_UNREADABLE",) and sid in g.text()
        assert f.is_file() and bu.start(confirmed=True).ok
        with pytest.raises(FileFormatError):
            bringup.build_package(folder, tmp_path / "o", data_dir=be.data_dir)
    finally:
        be.shutdown()


# ------------------------------------------------------------------------------------------------ SWD-P3-09

@pytest.mark.req("SW-PLT-003", "SW-BRU-002")
def test_fw_restart_with_default_parameters_is_not_an_event_loss() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        lost = []
        be.events.subscribe("log", lambda r: lost.append(r.payload) if r.payload.get("kind") == "FW_EVENTS_LOST"
                            else None)
        be.sim.act("estop", open=True)                               # a few EVENTs: SEQ > 0
        h.advance(300)
        n = be.pipeline.counters.events_lost
        be.sim.act("reset", cause="pin")                              # PARAMS_DEFAULTED (SEQ 0), BOOT (SEQ 1)
        h.advance(3000)
        assert not lost and be.pipeline.counters.events_lost == n
    finally:
        be.shutdown()


@pytest.mark.req("SW-PLT-003", "IF-007")
def test_restart_baseline_versus_a_real_gap() -> None:
    from bend_stand.core.pipeline import BOOT_TIME_EVENTS

    from .test_pipeline import Rig

    assert pg.Event.PARAMS_DEFAULTED in BOOT_TIME_EVENTS and pg.Event.HOMED not in BOOT_TIME_EVENTS
    r = Rig()
    r.event(5, pg.Event.ESTOP_SET)
    r.event(0, pg.Event.PARAMS_DEFAULTED)                             # FW restart: SEQ 0, then BOOT 1
    r.event(1, pg.Event.BOOT)
    r.event(2, pg.Event.DRIVER_DISABLED)
    r.p.step()
    assert r.p.counters.events_lost == 0 and r.lost_calls == 0
    r.event(0, pg.Event.HOMED)                                        # SEQ 0 of a normal EVENT after 2: a loss
    r.event(4, pg.Event.STOPPED)
    r.p.step()
    assert r.p.counters.events_lost == 253 + 3 and r.lost_calls == 2


# ------------------------------------------------------------------------------------------------ OBS-F-BRU-01

@pytest.mark.req("FW-SW-004", "SW-BRU-001")
def test_simulator_alm_follows_the_driver_supply() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        alm = 1 << pg.IO_BITS.index("ALM")

        def io_alm() -> bool:
            h.result(be._job(be.device.get_status_job))  # noqa: SLF001
            return bool(be.device.board.io & alm)

        assert not io_alm()
        be.sim.act("drv_power", on=False)                             # 48 V off: open ALM output reads "alarm"
        h.advance(100)
        assert io_alm()
        be.sim.act("drv_power", on=True)
        h.advance(500)
        assert not io_alm()
        be.sim.act("estop", open=True)                                # vocabulary default drv_power_follows
        h.advance(200)
        assert io_alm()
        be.sim.act("estop", open=False)                               # supply returns k1_delay_ms after closing
        h.advance(500)
        assert not io_alm() and be.sim.board.world.drv_power
        be.sim.act("estop", open=True, drv_power_follows=False)       # release 1 wiring: 48 V stays
        h.advance(200)
        assert not io_alm()
    finally:
        be.shutdown()

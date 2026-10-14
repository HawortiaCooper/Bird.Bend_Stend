"""Bring-up assistant engine (D-56 a, SRS SW-BRU-001…004; SW_design §25): step-file schema + semantics, condition
and entry vectors, a simulator walk-through of every step kind, the pulse-integrity check (D-56 b, SYS-011, E's
rules), kill / resume, the package (manifest hashes, HTML, JSON schema, data-folder rule), the CLI, and the gate
refusals through the assistant (identical to the normal API, no motion without an explicit confirmed click).

Verifies: SW-BRU-001, SW-BRU-002, SW-BRU-003, SW-BRU-004, SYS-011
"""
from __future__ import annotations

import copy
import html.parser
import json
import os
import zipfile
from pathlib import Path

import pytest
from bbs_support import lockstep_backend, tx_frames

from bend_stand.core import api, bringup
from bend_stand.core import protocol_gen as pg
from bend_stand.core.bringup import (
    BuField, EvalContext, apply_op, check_definitions, check_entries, eval_condition, load_definitions,
)
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import IssueSeverity, MotionKind

SHIPPED = json.loads(bringup.resource_path(bringup.STEPS_FILE).read_text(encoding="utf-8"))
#: the engine tests run against this fixture (former 28-step example + 10.2.4 step source, min_s, required_when);
#: the shipped file (A's content) has its own tests below
EXAMPLE_PATH = Path(__file__).parent / "data" / "bringup_steps_example.json"
EXAMPLE = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


# ============================================================================================ helpers

def _steps_file(tmp_path: Path, **pulse) -> str:
    doc = copy.deepcopy(EXAMPLE)
    for s in doc["steps"]:
        if s["kind"] == "pulse_integrity":
            s["params"].update({"round_trips": 1, "from_mm": 5, "to_mm": 45, **pulse})
    p = tmp_path / "steps.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _be(tmp_path: Path, **pulse):
    return lockstep_backend(recordings_root=str(tmp_path / "rec"), bringup_steps=_steps_file(tmp_path, **pulse))


def _ready(be, *, home: bool = True) -> None:
    """Enabled, fast homing, no-specimen mode, homed (the precondition of the motion steps)."""
    h = be.test_hooks
    g = be.motion.enable()
    assert g.ok, g
    assert h.run_until(lambda: be.status().motion.enabled, 2000)
    h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
    assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
    if home:
        h.result(be.motion.home(load_confirmed=True), 60_000)
        assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)


def _st(be, sid: str) -> str:
    return be.bringup.step(sid).state


def _wait_state(be, sid: str, states=("PASS", "FAIL"), ms: float = 3000) -> str:
    assert be.test_hooks.run_until(lambda: _st(be, sid) in states, ms), be.bringup.step(sid)
    return _st(be, sid)


def _wait_action(be, sid: str, ms: float = 400_000) -> str:
    assert be.test_hooks.run_until(lambda: be.bringup.step(sid).action_state in ("PASS", "FAIL", "ABORTED"), ms), \
        be.bringup.step(sid)
    return be.bringup.step(sid).action_state


def _motion_frames(be) -> int:
    return sum(len(tx_frames(be, c)) for c in (pg.Cmd.MOVE_ABS, pg.Cmd.HOME, pg.Cmd.JOG))


def _session_json(be) -> dict:
    be.bringup.flush()
    return json.loads((Path(be.bringup.status().folder) / "session.json").read_text(encoding="utf-8"))


PULSE = "10.2.3"


# ============================================================================================ definitions

#: the agreed pulse-integrity numbers (SYS-011, D-56 b, Validator E; B-BRU-04 item 1: exactly 30 mm/s)
AGREED_PULSE = {"baseline_homes": 3, "baseline_max_um": 100, "round_trips": 10, "from_mm": 5, "to_mm": 285,
                "speed_mm_s": 30, "d_floor_um": 30, "d_factor": 2, "repeats_allowed": 1}


@pytest.mark.req("SW-BRU-001", "SYS-011")
def test_shipped_step_file_is_valid_unique_covers_every_kind_and_agreed_pulse_numbers() -> None:
    assert check_definitions(SHIPPED) == []
    d = load_definitions()
    assert {s.kind for s in d.steps} == set(bringup.KINDS)
    assert len(set(d.ids)) == len(d.ids)
    pulses = [s for s in d.steps if s.kind == "pulse_integrity"]
    assert len(pulses) == 1 and pulses[0].engine_action and pulses[0].motion
    for k, v in AGREED_PULSE.items():                   # the numbers live in the step file (E's review)
        assert pulses[0].params[k] == v, k
    assert all(k in pulses[0].params for k in bringup.PULSE_PARAMS)
    assert all(s.action is None or s.action in bringup.GUI_ACTIONS + bringup.ENGINE_ACTIONS
               or s.action.startswith("show:") for s in d.steps)


@pytest.mark.req("SW-BRU-001")
def test_example_fixture_is_valid_and_covers_every_kind() -> None:
    assert check_definitions(EXAMPLE) == []
    d = load_definitions(EXAMPLE_PATH)
    assert {s.kind for s in d.steps} == set(bringup.KINDS) and d.step(PULSE).engine_action


def _mut(fn):
    doc = copy.deepcopy(EXAMPLE)
    fn(doc)
    return doc


def _step(doc, sid):
    return next(s for s in doc["steps"] if s["id"] == sid)


BAD = {
    "duplicate_id": lambda d: d["steps"].append(copy.deepcopy(d["steps"][0])),
    "unknown_section": lambda d: d["steps"][0].update(section="99"),
    "unknown_action": lambda d: d["steps"][0].update(action="jog_forever"),
    "unknown_tab": lambda d: d["steps"][0].update(action="show:nowhere"),
    "unknown_kind": lambda d: d["steps"][0].update(kind="magic"),
    "unknown_bit": lambda d: _step(d, "7.4")["auto"]["stages"][0]["all"][0].update(key="NO_SUCH_BIT"),
    "unknown_event": lambda d: _step(d, "7.1")["auto"]["stages"][0]["all"][0].update(key="NO_SUCH_EVENT"),
    "unknown_param": lambda d: _step(d, "6.7")["auto"]["stages"][0]["all"][0].update(key="no.such"),
    "unknown_indicator": lambda d: _step(d, "6.8")["auto"]["stages"][0]["all"][0].update(key="nope"),
    "bad_const": lambda d: _step(d, "6.3")["auto"]["stages"][0]["all"][2].update(value={"$const": "X"}),
    "seen_on_status": lambda d: _step(d, "7.4")["auto"]["stages"][0]["all"][0].update(op="seen"),
    "pulse_param_missing": lambda d: _step(d, PULSE)["params"].pop("baseline_max_um"),
    "pulse_bad_speed": lambda d: _step(d, PULSE)["params"].update(speed_mm_s="fast"),
    "pulse_wrong_action": lambda d: _step(d, PULSE).update(action="home"),
    "wizard_without_wizard": lambda d: (_step(d, "11.2.3")["params"].pop("wizard"),
                                        _step(d, "11.2.3").update(action=None)),
    "measure_without_number": lambda d: _step(d, "11.3.2").update(fields=[]),
    "choice_without_choices": lambda d: _step(d, "6.9")["fields"][0].update(choices=[]),
    "min_above_max": lambda d: _step(d, "11.3.2")["fields"][0].update(min=200),
    "unknown_key": lambda d: d["steps"][0].update(colour="red"),
    "wrong_schema": lambda d: d.update(schema_version=2),
    "pulse_speed_travel_limit": lambda d: _step(d, PULSE)["params"].update(speed_mm_s="travel_limit"),
    "step_source_unknown_id": lambda d: _step(d, "10.2.4")["auto"]["stages"][0]["all"][0].update(key="99.9"),
    "step_source_self": lambda d: _step(d, "10.2.4")["auto"]["stages"][0]["all"][0].update(key="10.2.4"),
    "path_on_other_src": lambda d: _step(d, "7.4")["auto"]["stages"][0]["all"][0].update(path="state"),
    "required_when_unknown_field": lambda d: _step(d, "6.9")["fields"][1]["required_when"].update(field="nope"),
    "required_when_bad_op": lambda d: _step(d, "6.9")["fields"][1]["required_when"].update(op="seen"),
    "min_s_negative": lambda d: _step(d, "8.3")["auto"]["stages"][0].update(min_s=-1),
}


@pytest.mark.req("SW-BRU-001")
@pytest.mark.parametrize("name", sorted(BAD))
def test_step_file_problems_are_refused(name, tmp_path) -> None:
    doc = _mut(BAD[name])
    assert check_definitions(doc), name
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(FileFormatError):
        load_definitions(p)


@pytest.mark.req("SW-BRU-001")
def test_unusable_step_file_refuses_the_session_but_not_the_backend(tmp_path) -> None:
    p = tmp_path / "x.json"
    p.write_text("{not json", encoding="utf-8")
    be = lockstep_backend(connect=False, bringup_steps=str(p))
    try:
        g = be.bringup.start()
        assert not g.ok and g.codes() == ("STEP_FILE_INVALID",)
        assert be.bringup.steps() == [] and "not usable" in be.bringup.status().definitions
    finally:
        be.shutdown()


# ============================================================================================ pure vectors

@pytest.mark.req("SW-BRU-001")
@pytest.mark.parametrize("op,a,b,want", [
    ("eq", 800, 800.0, True), ("eq", "0.8.6", "0.8.6", True), ("eq", None, None, False), ("ne", 1, 2, True),
    ("lt", 1, 2, True), ("le", 2, 2, True), ("gt", 3, 2, True), ("ge", 1, 2, False), ("lt", "a", 2, False),
    ("in", 2, [1, 2], True), ("not_in", 3, [1, 2], True), ("contains", ["AFE", "NVM"], "NVM", True),
    ("contains", 5, 5, False), ("regex", "20261010-2135", r"^2026\d{4}-\d{4}$", True), ("between", 80.4, [76, 84], True),
    ("between", 90, [76, 84], False), ("present", 0, None, True), ("absent", None, None, True),
    ("set", True, None, True), ("clear", False, None, True), ("clear", None, None, False), ("on", "ON", None, True),
    ("off", "UNKNOWN", None, False), ("eq", True, 1, True), ("eq", float("nan"), 1.0, False),
])
def test_apply_op_vectors(op, a, b, want) -> None:
    assert apply_op(op, a, b) is want


class _Ev:
    def __init__(self, name, arg=0, arg_name=None, value=0):
        self.name, self.arg, self.arg_name, self.value = name, arg, arg_name, value


@pytest.mark.req("SW-BRU-001")
def test_eval_condition_events_topics_bits_refs() -> None:
    ctx = EvalContext(flags=1 << pg.DATA_FLAGS_BITS.index("HOMED"),
                      data_status=1 << pg.DATA_STATUS_BITS.index("LIMIT_START"),
                      params={"home.drift_tol_um": 200}, events=[_Ev("FAULT_SET", 7, "HOME_DRIFT", 300),
                                                                 _Ev("HOMED", 0, None, -12)],
                      topics={"stop.confirmed"}, nvm={"matches_last_save": True}, constants={"X": 5})
    ev = lambda **c: eval_condition({"src": "event", **c}, ctx)  # noqa: E731
    assert ev(key="HOMED", op="seen") == (True, -12)
    assert ev(key="FAULT_SET", op="seen", arg="HOME_DRIFT")[0]
    assert not ev(key="FAULT_SET", op="seen", arg="LOAD_LIMIT")[0]
    assert ev(key="ESTOP_SET", op="not_seen")[0]
    assert ev(key="HOMED", op="between", value=[-30, 30])[0]
    assert eval_condition({"src": "topic", "key": "stop.confirmed", "op": "seen"}, ctx)[0]
    assert eval_condition({"src": "data_flag", "key": "HOMED", "op": "set"}, ctx)[0]
    assert eval_condition({"src": "data_status", "key": "LIMIT_START", "op": "set"}, ctx)[0]
    assert eval_condition({"src": "data_flag", "key": "MOVING", "op": "clear"}, ctx)[0]
    assert eval_condition({"src": "fault", "key": "HOME_DRIFT", "op": "clear"}, ctx) == (False, None)  # no STATUS
    assert eval_condition({"src": "param", "key": "home.drift_tol_um", "op": "eq", "value": 200}, ctx)[0]
    assert eval_condition({"src": "param", "key": "home.drift_tol_um", "op": "ge",
                           "value": {"$param": "home.drift_tol_um"}}, ctx)[0]
    assert eval_condition({"src": "nvm", "key": "matches_last_save", "op": "eq", "value": True}, ctx)[0]
    assert eval_condition({"src": "info", "key": "uid", "op": "present"}, ctx) == (False, None)
    assert bringup.cond_text({"src": "event", "key": "HOMED", "op": "seen", "label": "d"}, -12) == "d seen (-12)"


@pytest.mark.req("SW-BRU-001")
def test_check_entries_vectors() -> None:
    f = (BuField("ok", "Turns freely", "confirm"), BuField("u", "U", "number", "V", 0.6, 2.0),
         BuField("dir", "Direction", "choice", choices=("away", "toward"), pass_values=("away",)),
         BuField("note", "Note", "text", required=False))
    issues, res = check_entries(f, {"ok": True, "u": 0.7, "dir": "away"})
    assert res == "PASS" and issues == []
    issues, res = check_entries(f, {"ok": True, "u": 0.5, "dir": "away"})
    assert res == "FAIL" and [i.code for i in issues] == ["OUT_OF_LIMITS"]
    issues, res = check_entries(f, {"ok": True, "u": 0.7, "dir": "toward"})
    assert res == "FAIL" and issues[0].code == "NOT_EXPECTED"
    issues, res = check_entries(f, {"ok": False, "u": 0.7, "dir": "away"})
    assert res is None and issues[0].code == "REQUIRED" and issues[0].severity == IssueSeverity.ERROR
    for bad, code in (({"u": "0.7"}, "NOT_A_NUMBER"), ({"u": float("nan")}, "NOT_A_NUMBER"),
                      ({"dir": "sideways"}, "NOT_A_CHOICE"), ({"zzz": 1}, "UNKNOWN_FIELD"), ({"note": 5}, "NOT_TEXT")):
        e = {"ok": True, "u": 0.7, "dir": "away", **bad}
        issues, res = check_entries(f, e)
        assert res is None and code in [i.code for i in issues], (bad, issues)


# ============================================================================================ walk-through

@pytest.mark.req("SW-BRU-001", "SW-BRU-002")
def test_walkthrough_automatic_steps_on_the_simulator(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        st = bu.status()
        assert st.active and st.current == "6.3" and st.board_uid == be.device.info.uid and st.total == 30
        bu.select("6.5")
        assert _wait_state(be, "6.5") == "PASS" and "rate" in bu.step("6.5").auto.text
        bu.select("6.7")
        assert _wait_state(be, "6.7") == "PASS"
        bu.select("6.8")
        assert _wait_state(be, "6.8") == "PASS"
        bu.select("7.1")                                              # E-stop latch
        assert bu.step("7.1").auto.state == "WAITING"
        h.advance(500)
        assert _st(be, "7.1") == "NOT_DONE"                           # nothing happens without the input
        be.sim.act("estop", open=True)
        assert _wait_state(be, "7.1") == "PASS"
        bu.select("7.2")                                              # release, clear
        be.sim.act("estop", open=False)
        h.advance(200)
        h.result(be.estop_clear_async(confirmed=True))
        assert _wait_state(be, "7.2") == "PASS"
        bu.select("7.3")                                              # PAUSE: two stages
        be.sim.act("button", name="pause", pressed=True)
        h.advance(100)
        be.sim.act("button", name="pause", pressed=False)
        assert h.run_until(lambda: bu.step("7.3").auto.text.startswith("now"), 2000), bu.step("7.3")
        assert be.resume().ok
        assert _wait_state(be, "7.3") == "PASS"
        bu.select("7.4")
        be.sim.act("limit", name="start", active=True)
        h.advance(200)
        be.sim.act("limit", name="start", active=None)
        assert _wait_state(be, "7.4") == "PASS"
        bu.select("8.3")
        assert _wait_state(be, "8.3") == "PASS"                       # ALM input clear on the simulator
        bu.select("8.5")
        assert _st(be, "8.5") == "NOT_DONE"
        assert be.motion.enable().ok                                  # the GUI's normal handler (action "enable")
        assert _wait_state(be, "8.5") == "PASS"
        bu.select("9.1")
        assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
        assert _wait_state(be, "9.1") == "PASS"
        bu.select("9.6")
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
        h.result(be.motion.home(load_confirmed=True), 60_000)         # normal API (GUI handler + C-01)
        assert _wait_state(be, "9.6") == "PASS"
        ses = _session_json(be)
        assert ses["steps"]["9.6"]["recordings"], "the motion step was recorded"
        h.advance(500)                                                # the recording stops after the step
        assert be.status().recording.state == "IDLE"
        st = bu.status()
        assert st.passed == 11 and st.failed == 0
        ev = (Path(st.folder) / "events.jsonl").read_text(encoding="utf-8")
        assert '"ESTOP_SET"' in ev and "bringup.data_bits" in ev and "bringup.status" in ev
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001")
def test_auto_fail_if_timeout_and_reset(tmp_path) -> None:
    doc = copy.deepcopy(EXAMPLE)
    _step(doc, "6.5")["auto"]["stages"][0]["all"][1]["value"] = [10, 20]     # never met → timeout
    _step(doc, "6.5")["auto"]["timeout_s"] = 2
    p = tmp_path / "s.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    be = lockstep_backend(bringup_steps=str(p))
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        bu.select("6.5")
        assert _wait_state(be, "6.5", ms=5000) == "FAIL"
        assert "timeout after 2 s" in bu.step("6.5").auto.text and "rate" in bu.step("6.5").auto.text
        bu.select("7.1")                                              # fail_if: another fault latched
        be.sim.act("wire", input="start", broken=True)
        be.sim.act("wire", input="end", broken=True)
        assert _wait_state(be, "7.1") == "FAIL" and "fault latched" in bu.step("7.1").auto.text
        assert bu.mark("7.1", "NOT_DONE").ok                          # reset → re-armed
        assert bu.step("7.1").auto.state == "WAITING" and _st(be, "7.1") == "NOT_DONE"
        h.advance(300)
        assert bu.step("7.1").auto.state == "WAITING"                 # old events are not re-used
        bu.select("6.3")
        assert bu.step("7.1").auto.state == "NA"                      # leaving the step disarms it
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001")
def test_operator_entries_marks_and_skip(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        bu = be.bringup
        assert bu.submit("6.9", {"sign": "increases"})[0].code == "NO_SESSION"
        assert bu.start().ok
        iss = bu.submit("6.9", {"sign": "decreases"})                    # required_when: Raw needed when reversed
        assert [i.code for i in iss] == ["REQUIRED"] and _st(be, "6.9") == "NOT_DONE"
        assert bu.submit("6.9", {"sign": "decreases", "raw_pull": -800}) == [] and _st(be, "6.9") == "PASS"
        assert bu.submit("6.9", {"sign": "increases"}) == [] and _st(be, "6.9") == "PASS"   # optional otherwise
        issues = bu.submit("8.6", {"free": False})
        assert issues[0].code == "REQUIRED" and _st(be, "8.6") == "NOT_DONE" and bu.step("8.6").entries == {}
        assert bu.submit("8.6", {"free": True}, "turned by hand") == [] and _st(be, "8.6") == "PASS"
        assert bu.step("8.6").remark == "turned by hand"
        assert bu.submit("10.1.3", {"u_ena": 0.45})[0].code == "OUT_OF_LIMITS" and _st(be, "10.1.3") == "FAIL"
        assert bu.submit("10.1.3", {"u_ena": 0.71}) == [] and _st(be, "10.1.3") == "PASS"
        assert bu.submit("9.3", {"direction": "toward START"})[0].code == "NOT_EXPECTED"
        assert _st(be, "9.3") == "FAIL"
        g = bu.mark("8.8", "SKIPPED")
        assert not g.ok and g.codes() == ("REASON_REQUIRED",)
        assert bu.mark("8.8", "SKIPPED", reason="no clip leads").ok
        s = bu.step("8.8")
        assert s.state == "SKIPPED" and s.skip_reason == "no clip leads" and s.manual
        assert _st(be, "13.1") == "NOT_DONE" and bu.mark("13.1", "PASS").ok and _st(be, "13.1") == "PASS"
        assert bu.mark("13.1", "NOT_DONE").ok and _st(be, "13.1") == "NOT_DONE" and not bu.step("13.1").manual
        assert not bu.mark("13.1", "MAYBE").ok and not bu.mark("99", "PASS").ok
        st = bu.status()
        assert (st.passed, st.failed, st.skipped) == (3, 1, 1)
        hist = _session_json(be)["steps"]["9.3"]["history"]
        assert hist[-1]["what"] == "ENTRIES" and hist[-1]["state"] == "FAIL"
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001")
def test_wizard_step_follows_the_wizard_topic(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        bu.select("11.3.1")
        assert bu.step("11.3.1").auto.state == "NA"
        g = be.tare()                                                 # the GUI's normal TARE handler
        assert h.run_until(lambda: be.tare_engine.state().phase in ("DONE", "ABORTED", "REFUSED"), 20_000)
        want = "PASS" if be.tare_engine.state().phase == "DONE" else "FAIL"
        assert _wait_state(be, "11.3.1") == want, (g, be.tare_engine.state())
        assert bu.step("11.3.1").action_state == want
        bu.select("11.2.3")                                           # a wizard of another step: not this one
        be.tare()
        h.advance(3000)
        assert bu.step("11.2.3").action_state is None
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001", "SW-BRU-002")
def test_nvm_save_and_verify_steps(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        bu.select("12.1")
        h.result(be.config.save_async(), 10_000)                      # the GUI's Save to NVM
        assert _wait_state(be, "12.1") == "PASS"
        ses = _session_json(be)
        tags = [x["tag"] for x in ses["param_dumps"]]
        assert tags[0] == "start" and "nvm_save" in tags
        dump = json.loads((Path(bu.status().folder) / ses["param_dumps"][-1]["file"]).read_text(encoding="utf-8"))
        assert dump["count"] == len(be.config.values()) > 40
        bu.select("12.4")
        h.result(be.config.read_all_async(), 10_000)
        h.advance(500)
        assert bu.step("12.4").auto.state == "WAITING"
        assert be.bringup._context().nvm["matches_last_save"] is True  # noqa: SLF001  (FW 0.8.6 ≠ simulator)
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000}))    # differs from the saved set
        ctx = be.bringup._context()  # noqa: SLF001
        assert ctx.nvm["matches_last_save"] is False and ctx.nvm["saves"] == 1
    finally:
        be.shutdown()


# ============================================================================================ pulse integrity

@pytest.mark.req("SW-BRU-004", "SYS-011")
def test_pulse_integrity_needs_a_confirmed_click_and_passes(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        bu.select(PULSE)
        n0 = _motion_frames(be)
        g = bu.run_action(PULSE)
        assert g.needs_confirmation and "BRINGUP_MOTION" in g.codes()
        assert "3 re-homes, 1 round trips 5 ↔ 45 mm at 30 mm/s" in g.text()
        h.advance(500)
        assert _motion_frames(be) == n0 and not be.status().motion.moving        # nothing moved
        assert bu.run_action(PULSE, confirmed=True).ok
        assert bu.status().action_running == PULSE
        assert not bu.run_action(PULSE, confirmed=True).ok                       # one at a time
        assert not bu.mark(PULSE, "PASS").ok and not bu.start(confirmed=True).ok
        assert _wait_action(be, PULSE) == "PASS", bu.step(PULSE)
        assert _st(be, PULSE) == "PASS" and "no HOME_DRIFT" in bu.step(PULSE).action_text
        res = _session_json(be)["steps"][PULSE]["action"]
        r = res["result"]
        assert len(r["d0_um"]) == 3 and r["d1_um"] is not None and r["moves_done"] == 2 and r["limit_um"] == 30
        assert r["speed_mm_s"] == pytest.approx(30.0)
        assert len(tx_frames(be, pg.Cmd.HOME)) >= 5 and len(tx_frames(be, pg.Cmd.MOVE_ABS)) >= 2
        h.advance(500)
        assert _session_json(be)["steps"][PULSE]["recordings"] and be.status().recording.state == "IDLE"
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011")
def test_pulse_integrity_drift_fail_one_repeat_then_buffer_verdict(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        assert bu.run_action(PULSE, confirmed=True).ok
        assert h.run_until(lambda: "round trip" in bu.step(PULSE).action_text, 60_000)
        be.sim.act("world_shift", um=60)                              # lost steps: |d1| ≈ 60 µm > 30 µm, no latch
        assert _wait_action(be, PULSE) == "FAIL"
        s = bu.step(PULSE)
        assert s.state == "FAIL" and "one repeat allowed" in s.action_text and "> 30 µm" in s.action_text
        assert bu.run_action(PULSE, confirmed=True).ok                # the one repeat
        assert h.run_until(lambda: "round trip" in bu.step(PULSE).action_text, 60_000)
        be.sim.act("world_shift", um=400)                             # HOME_DRIFT latch (> 200 µm)
        assert _wait_action(be, PULSE) == "FAIL"
        s = bu.step(PULSE)
        assert "HOME_DRIFT" in s.action_text and "buffer board required (SYS-011)" in s.action_text
        g = bu.run_action(PULSE, confirmed=True)
        assert g.codes() == ("REPEAT_EXHAUSTED",)
        att = _session_json(be)["steps"][PULSE]["action"]["attempts"]
        assert [a["verdict"] for a in att] == ["DRIFT", "DRIFT"]
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011")
def test_pulse_integrity_baseline_scatter_stops_with_switch_mounting(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        assert bu.run_action(PULSE, confirmed=True).ok
        be.sim.act("world_shift", um=150)                             # baseline re-home 1 sees ≈ 150 µm
        assert _wait_action(be, PULSE) == "FAIL"
        s = bu.step(PULSE)
        assert "check the START switch mounting" in s.action_text and "buffer board required" not in s.action_text
        act = _session_json(be)["steps"][PULSE]["action"]
        assert act["result"]["verdict"] == "BASELINE_SCATTER" and act["verdict_fails"] == 0
        assert len(tx_frames(be, pg.Cmd.MOVE_ABS)) == 0               # no round trip after a bad baseline
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011", "SW-BRU-004")
@pytest.mark.parametrize("what", ["estop", "alm", "link"])
def test_pulse_integrity_disturbance_discards_the_readings(tmp_path, what) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        assert bu.run_action(PULSE, confirmed=True).ok
        assert h.run_until(lambda: "round trip" in bu.step(PULSE).action_text, 60_000)
        if what == "estop":
            be.sim.act("estop", open=True)
        elif what == "alm":
            be.sim.act("alm", active=True)
        else:
            be.sim.act("inject", fault="hang", duration_ms=2500)
        assert _wait_action(be, PULSE, 10_000) == "ABORTED"
        s = bu.step(PULSE)
        assert s.state == "NOT_DONE" and "run the check again from the baseline" in s.action_text
        act = _session_json(be)["steps"][PULSE]["action"]
        assert act["verdict_fails"] == 0 and act["attempts"][-1]["state"] == "ABORTED"
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-004", "SYS-011")
def test_pulse_integrity_refusals_are_the_normal_gates(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        n0 = _motion_frames(be)
        g = bu.run_action(PULSE, confirmed=True)                      # not enabled / not homed / no load input
        assert g == be.motion.check(MotionKind.MOVE, target_mm=45.0) and not g.ok
        _ready(be, home=False)
        g = bu.run_action(PULSE, confirmed=True)
        assert "NOT_HOMED" in g.codes() and g == be.motion.check(MotionKind.MOVE, target_mm=45.0)
        assert _motion_frames(be) == n0 and _st(be, PULSE) == "NOT_DONE"
        assert bu.run_action("9.6").codes() == ("NOT_AN_ENGINE_ACTION",)   # HOME runs through the GUI handler
        assert bu.run_action("nope").codes() == ("UNKNOWN_STEP",)
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011", "SW-BRU-004")
def test_pulse_integrity_speed_above_the_limits_is_refused_without_fail(tmp_path) -> None:
    """B-BRU-04 item 1: exactly the step speed; limits below it → refused, no FAIL, no limit raised."""
    be = _be(tmp_path, speed_mm_s=40)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        n0 = _motion_frames(be)
        g = bu.run_action(PULSE, confirmed=True)
        assert g.codes() == ("SPEED_BELOW_QUALIFICATION",) and "needs 40 mm/s" in g.text() and "30 mm/s" in g.text()
        s = bu.step(PULSE)
        assert s.state == "NOT_DONE" and s.action_state is None and _motion_frames(be) == n0
        assert be.motion.limits().v_travel_mm_s == pytest.approx(30.0)            # no limit was changed
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011")
def test_pulse_integrity_runs_exactly_the_step_speed(tmp_path) -> None:
    be = _be(tmp_path, speed_mm_s=20)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        assert bu.run_action(PULSE, confirmed=True).ok
        assert _wait_action(be, PULSE) == "PASS"
        r = _session_json(be)["steps"][PULSE]["action"]["result"]
        assert r["speed_mm_s"] == 20
        mv = tx_frames(be, pg.Cmd.MOVE_ABS)
        assert mv and all(int.from_bytes(f.frame[10:14], "little") == 20_000 for f in mv[-2:]), [f.frame.hex() for f in mv]
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-004")
def test_assistant_never_moves_by_itself(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        _ready(be, home=False)
        n0 = _motion_frames(be)
        assert bu.start().ok
        for sid in [s.id for s in bu.steps()]:
            bu.select(sid)
            bu.submit(sid, {})
            h.advance(150)
        bu.mark("9.6", "PASS")
        bu.mark("9.6", "NOT_DONE")
        h.advance(1000)
        assert _motion_frames(be) == n0 and not be.status().motion.moving
        assert bu.status().current == bu.steps()[-1].id
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-004")
def test_cancel_action_stops_through_the_normal_stop(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        assert bu.cancel_action() is None
        assert bu.run_action(PULSE, confirmed=True).ok
        assert h.run_until(lambda: "round trip" in bu.step(PULSE).action_text, 60_000)
        h.advance(200)
        assert be.status().motion.moving
        bu.cancel_action()
        assert h.run_until(lambda: not be.status().motion.moving, 3000)
        assert bu.step(PULSE).action_state == "ABORTED" and "cancelled" in bu.step(PULSE).action_text
    finally:
        be.shutdown()


# ============================================================================================ persistence

@pytest.mark.req("SW-BRU-002")
def test_kill_and_restart_resumes_the_session_without_loss(tmp_path) -> None:
    be1 = _be(tmp_path)
    h = be1.test_hooks
    bu = be1.bringup
    assert bu.start().ok
    sid = bu.status().session_id
    assert bu.submit("6.9", {"sign": "increases", "raw_pull": 1234}, "by hand") == []
    assert bu.mark("13.1", "SKIPPED", reason="later").ok
    bu.select("7.1")
    be1.sim.act("estop", open=True)
    assert _wait_state(be1, "7.1") == "PASS"
    _ready_ok = True
    be1.sim.act("estop", open=False)
    h.advance(200)
    h.result(be1.estop_clear_async(confirmed=True))
    assert h.run_until(lambda: be1.status().indicators["estop"].state == "OFF", 2000)
    _ready(be1)
    assert bu.run_action(PULSE, confirmed=True).ok                    # killed while the check runs
    assert h.run_until(lambda: "round trip" in bu.step(PULSE).action_text, 60_000)
    h.advance(300)                                                    # the writer ran (100 ms period)
    # "kill": no shutdown of be1 — a second backend on the same data folder (application / PC restart)
    be2 = lockstep_backend(connect=False, recordings_root=str(tmp_path / "rec"),
                           bringup_steps=_steps_file(tmp_path))
    try:
        st = be2.bringup.status()
        assert st.active and st.session_id == sid and st.current == "7.1" and _ready_ok
        s = be2.bringup.step("6.9")
        assert s.state == "PASS" and s.entries == {"sign": "increases", "raw_pull": 1234} and s.remark == "by hand"
        assert be2.bringup.step("13.1").skip_reason == "later" and be2.bringup.step("7.1").state == "PASS"
        p = be2.bringup.step(PULSE)
        assert p.action_state == "ABORTED" and "application restart" in p.action_text and p.state == "NOT_DONE"
        lines = (Path(st.folder) / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) > 20 and all(json.loads(x) for x in lines)
        log = [x["what"] for x in _session_json(be2)["log"]]
        assert log[0] == "SESSION_START" and log[-1] == "SESSION_RESUMED"
    finally:
        be2.shutdown()
        be1.shutdown()


@pytest.mark.req("SW-BRU-002")
def test_new_session_confirmation_close_and_resume(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        bu = be.bringup
        assert not bu.close_session().ok
        assert bu.start().ok
        first = bu.status().session_id
        bu.mark("13.1", "PASS")
        g = bu.start()
        assert g.needs_confirmation and g.codes() == ("SESSION_EXISTS",) and "1 steps done" in g.text()
        be.clock.advance(2.0)
        assert bu.start(confirmed=True).ok
        second = bu.status().session_id
        assert second != first and bu.status().done == 0
        old = json.loads((bringup.sessions_root(be.data_dir) / first / "session.json").read_text(encoding="utf-8"))
        assert old["closed_utc"] is not None and old["param_dumps"][-1]["tag"] == "end"
        assert bu.close_session().ok and not bu.status().active
        assert {x["session_id"] for x in bu.sessions()} == {first, second}
        assert bu.resume(first).ok and bu.status().session_id == first and bu.status().active
        assert bu.step("13.1").state == "PASS"
        assert not bu.resume("bu-nope").ok
    finally:
        be.shutdown()


# ============================================================================================ package

class _HtmlCheck(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        self.tags.append(tag)
        assert tag not in ("script", "iframe", "link", "img"), tag

    def handle_data(self, data) -> None:
        self.text.append(data)


@pytest.mark.req("SW-BRU-003", "SW-BRU-002")
def test_package_manifest_html_json_and_data_folders(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        logs = be.data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "bend_stand.log").write_text("incident line\n", encoding="utf-8")
        (be.data_dir / "gui.ini").write_text("[ui]\nunits=N\n", encoding="utf-8")
        cal = be.data_dir / "calibration"
        cal.mkdir(parents=True, exist_ok=True)
        (cal / "load_x.json").write_text("{}", encoding="utf-8")
        assert bu.start().ok
        bu.submit("6.9", {"sign": "increases"}, "<b>not html</b>")
        _ready(be)
        bu.select("9.6")
        h.result(be.motion.home(load_confirmed=True), 60_000)
        assert _wait_state(be, "9.6") == "PASS"
        bu.select("13.1")
        h.advance(500)
        outside = tmp_path / "elsewhere" / "rec_x"                    # a folder outside every data folder
        outside.mkdir(parents=True)
        (outside / "secret.csv").write_text("x", encoding="utf-8")
        with bu._lock:  # noqa: SLF001
            bu._session["recordings"].append({"step": "9.6", "folder": str(outside), "root": None})  # noqa: SLF001
        pkg = h.result(bu.create_package_async(), 30_000)
        p = Path(pkg.path)
        uid, build = be.device.info.uid, be.device.info.build
        assert p.parent == Path(os.environ["BEND_STAND_BRINGUP_DIR"])
        assert p.name.startswith("bringup_") and f"_{uid}_" in p.name and p.name.endswith(".zip")
        assert pkg.partial and pkg.files > 8 and pkg.size_bytes == p.stat().st_size and pkg.send_to
        assert bringup.verify_package(p) == []
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
            rec = json.loads(zf.read("bringup.json"))
            page = zf.read("report.html").decode("utf-8")
        assert {"manifest.json", "bringup.json", "report.html", "session/session.json", "session/events.jsonl",
                "logs/bend_stand.log", "settings/gui.ini", "calibration/load_x.json",
                "session/steps_used.json"} <= names
        assert any(n.startswith("session/params/params_001_start") for n in names)
        assert any(n.startswith("recordings/") for n in names) and not any("secret.csv" in n for n in names)
        assert any("outside the data folders" in n for n in rec["notes"])
        assert rec["versions"]["board_uid"] == uid and rec["versions"]["fw_build"] == build
        assert rec["partial"] and rec["summary"]["pass"] >= 2
        chk = _HtmlCheck()
        chk.feed(page)
        assert "table" in chk.tags and "<b>not html</b>" in "".join(chk.text) and "<b>not" not in page
        assert all(s.id in page for s in bu.steps())
        assert bu.status().last_package == pkg.path
        # tampering is detected
        bad = tmp_path / "bad.zip"
        with zipfile.ZipFile(p) as zi, zipfile.ZipFile(bad, "w") as zo:
            for n in zi.namelist():
                data = zi.read(n)
                zo.writestr(n, data + b"x" if n == "session/session.json" else data)
            zo.writestr("extra.txt", "x")
        probs = bringup.verify_package(bad)
        assert "hash mismatch: session/session.json" in probs and "not in the manifest: extra.txt" in probs
        pkg2 = h.result(bu.create_package_async(), 30_000)          # a second package gets its own name
        assert pkg2.path != pkg.path and Path(pkg2.path).name.endswith("_2.zip")
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-003")
def test_package_record_violating_the_schema_is_refused(tmp_path, monkeypatch) -> None:
    be = _be(tmp_path)
    try:
        assert be.bringup.start().ok
        be.bringup.flush()
        monkeypatch.setattr(bringup, "build_record", lambda *a, **k: {"schema": "x"})
        with pytest.raises(FileFormatError, match="does not validate"):
            bringup.build_package(be.bringup.status().folder, tmp_path / "o", data_dir=be.data_dir)
        assert not (tmp_path / "o").exists() or not list((tmp_path / "o").glob("*.zip"))
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-003")
def test_cli_bringup_export_and_verify(tmp_path, capsys) -> None:
    from bend_stand.__main__ import main

    be = _be(tmp_path)
    try:
        assert be.bringup.start().ok
        be.bringup.mark("13.1", "PASS")
        be.bringup.flush()
    finally:
        be.shutdown()
    out = tmp_path / "cli"
    assert main(["bringup-export", "--out", str(out)]) == 0                  # the active session, offline
    zips = list(out.glob("bringup_*.zip"))
    assert len(zips) == 1 and str(zips[0]) in capsys.readouterr().out
    assert main(["bringup-export", "--verify", str(zips[0])]) == 0
    assert "package OK" in capsys.readouterr().out
    assert main(["bringup-export", "bu-none", "--out", str(out)]) == 2
    bad = tmp_path / "b.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"files": [{"path": "a", "size": 1, "sha256": "0"}]}))
    assert main(["bringup-export", "--verify", str(bad)]) == 1


@pytest.mark.req("SW-BRU-003")
def test_package_of_the_last_session_without_an_open_one(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        f = bu.create_package_async()
        with pytest.raises(FileNotFoundError):
            h.result(f)
        assert bu.start().ok and bu.close_session().ok
        pkg = h.result(bu.create_package_async(), 30_000)
        with zipfile.ZipFile(pkg.path) as zf:
            assert json.loads(zf.read("bringup.json"))["session"]["closed_utc"] is not None
    finally:
        be.shutdown()


# ============================================================================================ API contract

@pytest.mark.req("SW-BRU-001", "SW-PLT-002")
def test_engine_conforms_to_the_bringup_protocol(tmp_path) -> None:
    import inspect

    be = _be(tmp_path)
    try:
        assert isinstance(be.bringup, api.BringupAPI)
        for name, fn in inspect.getmembers(api.BringupAPI, inspect.isfunction):
            if name.startswith("_"):
                continue
            want = [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values() if p.name != "self"]
            got = [(p.name, p.kind, p.default) for p in inspect.signature(getattr(be.bringup, name)).parameters.values()]
            extra = got[len(want):]                           # B-only keyword extras with defaults (mark origin)
            assert got[:len(want)] == want, name
            assert all(k == inspect.Parameter.KEYWORD_ONLY and d is not inspect.Parameter.empty
                       for _n, k, d in extra), (name, extra)
        events = []
        be.events.subscribe("bringup.step", events.append)
        be.events.subscribe("bringup.state", events.append)
        assert be.bringup.start().ok
        be.bringup.mark("13.1", "PASS")
        be.test_hooks.advance(200)
        topics = {e.topic for e in events}
        assert topics == set(api.BRINGUP_TOPICS)
        assert any(isinstance(e.payload, api.BuStep) and e.payload.id == "13.1" for e in events)
        assert set(api.BRINGUP_TOPICS) <= set(api.TOPICS) and api.BringupAPI in api.PROTOCOLS
        assert isinstance(be, api.BackendAPI)
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-002", "SW-BRU-003")
@pytest.mark.rt
def test_real_clock_writer_thread_resume_and_package(tmp_path) -> None:
    """The writer thread (real clock) writes events / session; a normal restart resumes; the package future works."""
    import time

    from bend_stand.core.backend import Backend, BackendSettings

    steps = _steps_file(tmp_path)
    be = Backend(BackendSettings(recordings_root=str(tmp_path / "rec"), bringup_steps=steps))
    be.start()
    try:
        be.connect_async("sim").result(timeout=10)
        assert be.bringup.start().ok
        be.bringup.select("6.5")
        t_end = time.monotonic() + 10
        while be.bringup.step("6.5").state == "NOT_DONE" and time.monotonic() < t_end:
            time.sleep(0.05)
        assert be.bringup.step("6.5").state == "PASS"
        assert be.bringup.submit("6.9", {"sign": "increases"}) == []
        folder = Path(be.bringup.status().folder)
        time.sleep(0.5)
        assert (folder / "events.jsonl").stat().st_size > 0
        pkg = be.bringup.create_package_async().result(timeout=30)
        assert bringup.verify_package(pkg.path) == []
        sid = be.bringup.status().session_id
    finally:
        be.shutdown()
    assert not any(t.name == "bend-bringup" and t.is_alive() for t in __import__("threading").enumerate())
    be2 = Backend(BackendSettings(recordings_root=str(tmp_path / "rec"), bringup_steps=steps))
    be2.start()
    try:
        st = be2.bringup.status()
        assert st.active and st.session_id == sid and be2.bringup.step("6.9").state == "PASS"
        assert be2.bringup.step("6.5").state == "PASS"
    finally:
        be2.shutdown()


@pytest.mark.req("SW-BRU-003", "SW-BRU-001")
def test_minischema_keywords() -> None:
    from bend_stand.core import minischema as ms

    s = {"$defs": {"n": {"type": "integer", "minimum": 0, "maximum": 9}},
         "type": "object", "required": ["a"], "additionalProperties": {"type": "string", "maxLength": 3},
         "properties": {"a": {"$ref": "#/$defs/n"}, "b": {"type": ["string", "null"], "minLength": 2, "pattern": "^x"},
                        "c": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"enum": [1, 2]}},
                        "d": {"anyOf": [{"type": "integer"}, {"const": "x"}]},
                        "e": {"oneOf": [{"type": "number"}, {"type": "integer"}]},
                        "f": {"allOf": [{"type": "number"}, {"minimum": 1}]}}}
    assert ms.validate({"a": 3, "b": "xy", "c": [1], "d": "x", "e": 1.5, "f": 2, "z": "abc"}, s) == []
    bad = ms.validate({"a": True, "b": "y", "c": [3, 1, 2], "d": "y", "e": 1, "f": 0, "z": "abcd"}, s)
    for frag in ("$.a: expected integer", "$.b: 'y' does not match", "$.b: shorter than 2", "more than 2 items",
                 "$.c[0]: 3 not one of", "$.d: matches 0 of the anyOf", "$.e: matches 2 of the oneOf",
                 "$.f: 0 < minimum 1", "$.z: longer than 3"):
        assert any(frag in e for e in bad), (frag, bad)
    assert ms.validate({}, s) == ["$: missing 'a'"]
    assert ms.validate({"a": 10}, s) == ["$.a: 10 > maximum 9"]
    assert ms.validate({"a": 1, "c": []}, s) == ["$.c: fewer than 1 items"]
    assert ms.validate(float("nan"), {"type": "number"}) != []
    assert ms.validate({"x": 1}, {"type": "object", "additionalProperties": False}) == ["$: unknown key 'x'"]
    assert len(ms.validate(list(range(100)), {"type": "array", "items": {"type": "string"}})) == ms.MAX_ERRORS
    with pytest.raises(ValueError):
        ms.validate(1, {"$ref": "http://x"})


@pytest.mark.req("SW-BRU-002", "SW-BRU-003")
def test_origin_recorded_and_cli_test_marks_visible_in_the_package(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        bu.select("6.5")
        assert _wait_state(be, "6.5") == "PASS" and bu.step("6.5").origin == "auto"
        assert bu.submit("6.9", {"sign": "increases"}) == [] and bu.step("6.9").origin == "operator"
        assert bu.mark("8.6", "PASS").ok and bu.step("8.6").origin == "operator"
        assert bu.mark("13.1", "PASS", remark="smoke", origin="cli-test").ok
        assert bu.step("13.1").origin == "cli-test" and bu.step("13.1").manual
        g = bu.mark("13.1", "PASS", origin="auto")
        assert g.codes() == ("BAD_ORIGIN",) and bu.step("13.1").origin == "cli-test"
        assert bu.step("7.1").origin == ""
        hist = _session_json(be)["steps"]["13.1"]["history"]
        assert hist[-1]["origin"] == "cli-test"
        pkg = h.result(bu.create_package_async(), 30_000)
        assert bringup.verify_package(pkg.path) == []
        with zipfile.ZipFile(pkg.path) as zf:
            rec = json.loads(zf.read("bringup.json"))
            page = zf.read("report.html").decode("utf-8")
        by = {s["id"]: s for s in rec["steps"]}
        assert by["13.1"]["origin"] == "cli-test" and by["6.5"]["origin"] == "auto"
        assert by["6.9"]["origin"] == "operator" and rec["summary"]["cli_test"] == 1
        assert rec["notes"][0].startswith("1 step(s) marked from the command line (origin cli-test")
        assert "origin cli-test, simulator smoke test" in page and "color:#b00000;font-weight:bold'>cli-test" in page
        assert bu.mark("13.1", "NOT_DONE").ok and bu.step("13.1").origin == ""
    finally:
        be.shutdown()


# ============================================================================================ B-BRU-04 (E's review)

def _pulse_running(be, ms: float = 60_000) -> None:
    bu = be.bringup
    assert bu.run_action(PULSE, confirmed=True).ok
    assert be.test_hooks.run_until(lambda: "round trip" in bu.step(PULSE).action_text, ms), bu.step(PULSE)


@pytest.mark.req("SW-BRU-001")
def test_stale_or_disconnected_data_never_satisfies_a_condition(tmp_path) -> None:
    """B-BRU-04 item 7: link CONNECTED + board data newer than the arming and the last connect / BOOT."""
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        h.result(be.disconnect_async())
        bu.select("6.8")                                              # all "off" conditions — default flags = 0
        h.advance(2000)
        assert bu.step("6.8").auto.state == "WAITING" and _st(be, "6.8") == "NOT_DONE"
        h.result(be.connect_async("sim"), 5000)
        assert _wait_state(be, "6.8") == "PASS"
        bu.select("7.4")
        with bu._lock:  # noqa: SLF001
            bu._epoch_ns = be.clock.monotonic_ns() + 10**15          # "a BOOT just happened" → all data is stale
        be.sim.act("limit", name="start", active=True)
        h.advance(500)
        assert bu.step("7.4").auto.text.startswith("press")          # not evaluated on stale data
        with bu._lock:  # noqa: SLF001
            bu._epoch_ns = 0
        h.advance(300)
        assert bu.step("7.4").auto.text.startswith("release")
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001")
def test_stage_min_s_needs_the_condition_held_continuously(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        be.sim.act("alm", active=True)
        h.advance(300)
        bu.select("8.3")                                              # ALM off held ≥ 1 s
        be.sim.act("alm", active=False)
        h.advance(600)
        be.sim.act("alm", active=True)                                # interrupted before 1 s
        h.advance(300)
        be.sim.act("alm", active=False)
        h.advance(700)
        assert _st(be, "8.3") == "NOT_DONE"
        assert _wait_state(be, "8.3", ms=2000) == "PASS" and "held ≥ 1 s" in bu.step("8.3").auto.text
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001", "SYS-011")
def test_step_source_follows_another_steps_verdict(tmp_path) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        bu.select("10.2.4")
        h.advance(500)
        assert bu.step("10.2.4").auto.state == "WAITING"
        _ready(be)
        assert bu.run_action(PULSE, confirmed=True).ok
        assert _wait_action(be, PULSE) == "PASS"
        assert _wait_state(be, "10.2.4") == "PASS" and "10.2.3 = PASS" in bu.step("10.2.4").auto.text
        assert bu.mark(PULSE, "FAIL").ok and bu.mark("10.2.4", "NOT_DONE").ok
        assert _wait_state(be, "10.2.4") == "FAIL"
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011", "SW-BRU-004")
@pytest.mark.parametrize("what,verdict", [("limit", "LIMIT"), ("step_fault", "FAULT"), ("reset", "BOOT"),
                                          ("e_range", "E_RANGE")])
def test_pulse_integrity_stop_conditions_fail_and_stop(tmp_path, what, verdict) -> None:
    """B-BRU-04 items 2 and 11: limit during a move, STEP_FAULT, a board restart and an E_RANGE refusal are FAIL +
    stop with report (no repeat in this session), not "run again"."""
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        if what == "e_range":
            be.sim.inject_nack("MOVE_ABS", "E_RANGE", count=1)
        if what == "limit":
            be.sim.act("limit", name="end", position_um=30_000)       # END switch inside the run's stroke
        assert bu.run_action(PULSE, confirmed=True).ok
        if what in ("step_fault", "reset"):
            assert h.run_until(lambda: "round trip" in bu.step(PULSE).action_text, 60_000)
            h.advance(200)
            if what == "step_fault":
                be.sim.act("inject", fault="step_fault")
            else:
                be.sim.act("reset", cause="pin")
        assert h.run_until(lambda: bu.step(PULSE).action_state in ("FAIL", "ABORTED", "PASS"), 120_000)
        h.advance(500)
        s = bu.step(PULSE)
        assert s.action_state == "FAIL" and s.state == "FAIL" and "STOP the bring-up and report" in s.action_text, s
        act = _session_json(be)["steps"][PULSE]["action"]
        assert act["hard_fail"] and act["attempts"][-1]["verdict"] == verdict and act["verdict_fails"] == 0, act
        if what == "e_range":
            assert "E_RANGE refusal (no limit is raised): NACK E_RANGE" in s.action_text
        assert bu.run_action(PULSE, confirmed=True).codes() == ("STOP_REPORTED",)
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011")
@pytest.mark.parametrize("what,name", [("pause", "PAUSE"), ("halt", "HALT")])
def test_pulse_integrity_pause_and_halt_abort_with_the_cause_named(tmp_path, what, name) -> None:
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        _pulse_running(be)
        h.advance(200)
        if what == "pause":
            be.pause("gui")
        else:
            be.halt("gui")
        assert _wait_action(be, PULSE, 10_000) == "ABORTED"
        assert name in bu.step(PULSE).action_text and "again from the baseline" in bu.step(PULSE).action_text
        assert not _session_json(be)["steps"][PULSE]["action"].get("hard_fail")
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011")
def test_pulse_integrity_home_drift_in_the_baseline_is_baseline_scatter(tmp_path) -> None:
    """B-BRU-04 item 10: HOME_DRIFT during the baseline → switch mounting, at the first event, not a repeat."""
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        assert bu.run_action(PULSE, confirmed=True).ok
        be.sim.act("world_shift", um=400)
        assert _wait_action(be, PULSE) == "FAIL"
        act = _session_json(be)["steps"][PULSE]["action"]
        assert act["result"]["verdict"] == "BASELINE_SCATTER" and act["verdict_fails"] == 0
        assert "HOME_DRIFT during the baseline" in act["text"] and len(act["result"]["d0_um"]) == 0
        assert "check the START switch mounting" in act["text"] and not act.get("hard_fail")
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011")
def test_pulse_integrity_rehome_needs_fresh_homed(tmp_path) -> None:
    """B-BRU-04 item 8: HOMED from data older than the last connect / BOOT never starts a re-home."""
    be = _be(tmp_path)
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok
        _ready(be)
        n = len(tx_frames(be, pg.Cmd.HOME))
        with bu._lock:  # noqa: SLF001
            bu._epoch_ns = be.clock.monotonic_ns() + 10**15
        assert bu.run_action(PULSE, confirmed=True).ok
        s = bu.step(PULSE)
        assert s.action_state == "ABORTED" and "HOMED not confirmed by fresh board data" in s.action_text
        assert len(tx_frames(be, pg.Cmd.HOME)) == n
    finally:
        be.shutdown()


# ============================================================================================ shipped file on the simulator

def _shipped_be():
    return lockstep_backend()                                     # shipped step file, temp folders


@pytest.mark.req("SW-BRU-001", "SW-BRU-004")
def test_shipped_file_smoke_every_auto_step_on_the_simulator() -> None:
    be = _shipped_be()
    try:
        h, bu = be.test_hooks, be.bringup
        assert bu.start().ok and bu.status().total == len(SHIPPED["steps"])
        n0 = _motion_frames(be)
        autos = [s.id for s in bu.steps() if s.auto is not None]
        assert autos
        for sid in autos:
            bu.select(sid)
            h.advance(300)
            s = bu.step(sid)
            assert s.state in bringup.STATES and s.auto.state in ("NA", "WAITING", "PASS", "FAIL"), s
        assert _motion_frames(be) == n0                              # selecting never moves anything
        h.advance(500)
        assert bringup.verify_package(h.result(bu.create_package_async(), 30_000).path) == []
    finally:
        be.shutdown()


@pytest.mark.req("SYS-011", "SW-BRU-004")
def test_shipped_pulse_integrity_step_runs_on_the_simulator() -> None:
    """The shipped pulse step (agreed numbers: 3 re-homes, 10 round trips 5 ↔ 285 mm at 30 mm/s) runs to PASS."""
    be = _shipped_be()
    try:
        h, bu = be.test_hooks, be.bringup
        sid = next(s.id for s in bu.steps() if s.step_kind == "pulse_integrity")
        assert bu.start().ok
        _ready(be)
        assert bu.run_action(sid).needs_confirmation
        assert bu.run_action(sid, confirmed=True).ok, bu.step(sid)
        assert _wait_action(be, sid, 600_000) == "PASS", bu.step(sid)
        r = _session_json(be)["steps"][sid]["action"]["result"]
        assert r["moves_done"] == 20 and len(r["d0_um"]) == 3 and r["speed_mm_s"] == 30 and r["limit_um"] >= 30
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001")
def test_cross_step_required_when_and_invalid_when(tmp_path) -> None:
    """A field may depend on another step's entries: the "no repeat needed" choice of 10.1.5 is refused after a
    0.50 V reading in 10.1.3, and the mA field becomes required (§25.1 ``invalid_when`` / ``required_when``)."""
    no_rep = "all readings ≥ 0.60 V (no repeat needed)"
    rep = "repeated with the mA range / 10 Ω shunt"
    be = _be(tmp_path)
    try:
        bu = be.bringup
        assert bu.start().ok
        iss = bu.submit("10.1.5", {"readings": no_rep})             # 10.1.3 not entered yet ("absent" rule)
        assert [i.code for i in iss] == ["INVALID"] and "below 0.60 V (or missing)" in iss[0].text
        assert bu.submit("10.1.3", {"u_ena": 0.50})[0].code == "OUT_OF_LIMITS"      # stored, step FAIL
        assert bu.submit("10.1.4", {"u_dir": 0.70}) == []
        iss = bu.submit("10.1.5", {"readings": no_rep})
        assert [i.code for i in iss] == ["INVALID", "REQUIRED"] and iss[1].key == "i_ena_ma"
        assert _st(be, "10.1.5") == "NOT_DONE" and bu.step("10.1.5").entries == {}
        iss = bu.submit("10.1.5", {"readings": rep})
        assert [(i.key, i.code) for i in iss] == [("i_ena_ma", "REQUIRED"), ("i_dir_ma", "REQUIRED")]
        assert bu.submit("10.1.5", {"readings": rep, "i_ena_ma": 6.4, "i_dir_ma": 7.0}) == []
        assert _st(be, "10.1.5") == "PASS"
        assert bu.submit("10.1.5", {"readings": rep, "i_ena_ma": 5.1, "i_dir_ma": 7.0})[0].code == "OUT_OF_LIMITS"
        assert _st(be, "10.1.5") == "FAIL"
        assert bu.submit("10.1.3", {"u_ena": 0.66}) == []                            # all readings fine now
        assert bu.submit("10.1.5", {"readings": no_rep}) == [] and _st(be, "10.1.5") == "PASS"
    finally:
        be.shutdown()


@pytest.mark.req("SW-BRU-001")
def test_entry_rule_vectors_and_definition_checks() -> None:
    f = (BuField("a", "A", "choice", choices=("x", "y")),
         BuField("b", "B", "number", required_when=({"step": "s1", "field": "u", "op": "lt", "value": 1},
                                                   {"field": "a", "op": "eq", "value": "y"})),
         BuField("c", "C", "text", required=False,
                 invalid_when=({"when": {"step": "s2", "field": "w", "op": "eq", "value": True}, "text": "no"},)))
    others = {"s1": {"u": 2}, "s2": {"w": False}}
    assert check_entries(f, {"a": "x"}, others) == ([], "PASS")
    assert check_entries(f, {"a": "y"}, others)[0][0].code == "REQUIRED"                  # same-step rule
    assert check_entries(f, {"a": "x"}, {"s1": {"u": 0.5}})[0][0].code == "REQUIRED"     # cross-step rule
    iss, res = check_entries(f, {"a": "x", "c": "any"}, {"s2": {"w": True}})
    assert res is None and iss[0].code == "INVALID" and iss[0].text == "C: no"            # any value refused
    assert check_entries(f, {"a": "x", "c": "any"}, None) == ([], "PASS")                 # no other entries
    for bad in ({"step": "99.9", "field": "u_ena", "op": "lt", "value": 1},
                {"step": "10.1.3", "field": "nope", "op": "lt", "value": 1},
                {"step": "10.1.5", "field": "readings", "op": "eq", "value": 1},
                {"step": "10.1.3", "field": "u_ena", "op": "on"}):
        doc = copy.deepcopy(EXAMPLE)
        _step(doc, "10.1.5")["fields"][1]["required_when"] = [bad]
        assert check_definitions(doc), bad
        doc = copy.deepcopy(EXAMPLE)
        _step(doc, "10.1.5")["fields"][0]["invalid_when"][0]["when"] = bad
        assert check_definitions(doc), bad
    doc = copy.deepcopy(EXAMPLE)
    _step(doc, "10.1.5")["fields"][0]["invalid_when"][0].pop("text")
    assert check_definitions(doc)

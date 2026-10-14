"""Level C / I — Bring-up assistant and bring-up package (SW-BRU-001…004, D-56 a) and the SYS-011 pulse-integrity
check (D-56 b) — Validator F, SW_test_plan v0.5.7 §3.15.

Ground truth (§1 rule 1): the wire (every frame re-parsed by the oracle ``ref_codec``), the simulator / twin world
(``world_shift``, the true carriage position), the delivered bring-up guide (step ids parsed here, independently of the
step file) and the package files re-read and **re-hashed by this module** (``zipfile`` + ``hashlib``) and checked
with F's own JSON-schema subset validator (:func:`f_validate`) — never the assistant's own ``verify_package`` or its
``minischema``. Expected limits come from the SRS (SYS-011: 3 baseline re-homes, max|d0| ≤ 100 µm, |d1| ≤
max(30 µm, 2·max|d0|), one repeat) and ``params.yaml`` (``motion.v_max_travel_um_s``, ``home.drift_tol_um``).

The step file is data (A's content, B's engine): every test reads the **shipped** file and finds its steps by kind /
field, not by a hard-coded id where the content may move (pulse integrity, I_LED); stimuli of the walk-through are
keyed by the guide step ids (stable ids, guide v0.3+).

Verifies: SW-BRU-001, SW-BRU-002, SW-BRU-003, SW-BRU-004, SYS-011
"""
from __future__ import annotations

import copy
import hashlib
import html.parser
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

import harness as H
import ref_codec as rc

SW = Path(__file__).resolve().parents[2]
REPO = SW.parent
RES = SW / "src" / "bend_stand" / "resources"
SHIPPED = RES / "bringup_steps.json"
STEPS_SCHEMA = RES / "bringup_steps.schema.json"
RECORD_SCHEMA = RES / "bringup_record.schema.json"
CHILD = Path(__file__).resolve().parent / "bru_child.py"

#: commands that move or arm the axis (SW-BRU-004: never sent by the assistant without an explicit click)
MOTION_CMDS = frozenset({"ENABLE", "DISABLE", "HOME", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD"})
#: commands that change the board configuration (never sent by the assistant itself)
CONFIG_CMDS = frozenset({"SET_PARAM", "SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS", "REBOOT"})
#: SYS-011 (SRS v0.6.8) numbers — the expected values of this module
SYS011 = {"baseline_homes": 3, "baseline_max_um": 100, "round_trips": 10, "from_mm": 5, "to_mm": 285,
          "d_floor_um": 30, "d_factor": 2.0, "repeats_allowed": 1}


# ============================================================================================ independent readers

def shipped_doc() -> dict:
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def guide_path() -> Path:
    found = sorted((REPO / "04_Delivery").glob("BringUp_FW*/BRINGUP_GUIDE.md"))
    assert found, "no delivered bring-up guide in 04_Delivery"
    return found[-1]


def guide_ids(text: str) -> list[str]:
    """Step ids of the guide: check-box items ``- [ ] **6.3**`` and the measurement table rows ``| 3.2-1 |``."""
    ids = []
    for ln in text.splitlines():
        m = re.match(r"^\s*- \[ \] \*\*(\d+(?:\.\d+)+)\*\*", ln)
        if m:
            ids.append(m.group(1))
            continue
        m = re.match(r"^\|\s*(\d+\.\d+-\d+)\s*\|", ln)
        if m:
            ids.append(m.group(1))
    return ids


def is_example(doc: dict) -> bool:
    return "example" in str(doc.get("guide", {}).get("content_version", "")).lower()


_ANNOT = {"$schema", "$id", "title", "description", "$comment", "examples", "default", "$defs"}
_KNOWN = {"type", "const", "enum", "required", "properties", "additionalProperties", "items", "$ref", "pattern",
          "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "anyOf", "oneOf"}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _is_type(v: Any, t: str) -> bool:
    return {"null": v is None, "boolean": isinstance(v, bool), "string": isinstance(v, str),
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool),
            "object": isinstance(v, dict), "array": isinstance(v, list)}[t]


def f_validate(doc: Any, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """Validator F's JSON-schema subset (draft 2020-12 keywords used by the shipped schemas). An unknown keyword is an
    error, so a schema change that this checker does not understand cannot pass silently."""
    root = schema if root is None else root
    errs: list[str] = []
    for k in schema:
        if k not in _ANNOT and k not in _KNOWN:
            errs.append(f"{path}: keyword {k!r} not implemented by F's validator")
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/$defs/"), ref
        errs += f_validate(doc, root["$defs"][ref.split("/")[-1]], root, path)
    if "type" in schema:
        ts = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(doc, t) for t in ts):
            return errs + [f"{path}: type {type(doc).__name__} not in {ts}"]
    if "const" in schema and not _same(doc, schema["const"]):
        errs.append(f"{path}: {doc!r} != const {schema['const']!r}")
    if "enum" in schema and not any(_same(doc, e) for e in schema["enum"]):
        errs.append(f"{path}: {doc!r} not in enum")
    if isinstance(doc, dict):
        for r in schema.get("required", ()):
            if r not in doc:
                errs.append(f"{path}: missing {r!r}")
        props = schema.get("properties", {})
        for k, v in doc.items():
            if k in props:
                errs += f_validate(v, props[k], root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected {k!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                errs += f_validate(v, schema["additionalProperties"], root, f"{path}.{k}")
    if isinstance(doc, list):
        if "minItems" in schema and len(doc) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(doc) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, v in enumerate(doc):
                errs += f_validate(v, schema["items"], root, f"{path}[{i}]")
    if isinstance(doc, str):
        if "minLength" in schema and len(doc) < schema["minLength"]:
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(doc) > schema["maxLength"]:
            errs.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], doc) is None:
            errs.append(f"{path}: {doc!r} does not match {schema['pattern']!r}")
    if _is_type(doc, "number"):
        if "minimum" in schema and doc < schema["minimum"]:
            errs.append(f"{path}: {doc} < {schema['minimum']}")
        if "maximum" in schema and doc > schema["maximum"]:
            errs.append(f"{path}: {doc} > {schema['maximum']}")
    for kw in ("anyOf", "oneOf"):
        if kw in schema:
            ok = [not f_validate(doc, s, root, path) for s in schema[kw]]
            if (kw == "anyOf" and not any(ok)) or (kw == "oneOf" and sum(ok) != 1):
                errs.append(f"{path}: {kw} not satisfied")
    return errs


def rehash_zip(path: str | Path) -> tuple[dict, dict[str, bytes]]:
    """(manifest, {member: bytes}) — F's own reading of the package; asserts the manifest covers every member."""
    with zipfile.ZipFile(path) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    assert "manifest.json" in members, sorted(members)
    man = json.loads(members["manifest.json"])
    listed = [f["path"] for f in man["files"]]
    assert len(listed) == len(set(listed)), "manifest lists a member twice"
    assert set(listed) == set(members) - {"manifest.json"}, (sorted(set(listed) ^ (set(members) - {"manifest.json"})))
    for f in man["files"]:
        data = members[f["path"]]
        assert f["size"] == len(data), f["path"]
        assert f["sha256"] == hashlib.sha256(data).hexdigest(), f"hash mismatch {f['path']}"
    return man, members


class _Html(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.bad: list[str] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        self.tags.append(tag)
        if tag in ("script", "iframe", "object", "embed", "link", "img", "base", "form"):
            self.bad.append(tag)
        for k, v in attrs:
            if k.lower().startswith("on") or (k in ("src", "href") and v and not v.startswith("#")):
                self.bad.append(f"{tag}[{k}={v}]")

    def handle_data(self, data):  # noqa: ANN001
        self.text.append(data)


# ============================================================================================ step-file helpers

def write_doc(tmp_path: Path, doc: dict, name: str = "steps.json") -> str:
    p = Path(tmp_path) / name
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


def pulse_id(doc: dict) -> str:
    ids = [s["id"] for s in doc["steps"] if s["kind"] == "pulse_integrity"]
    assert len(ids) == 1, ids
    return ids[0]


def pulse_doc(**params: Any) -> tuple[dict, str]:
    """The shipped file with pulse-integrity parameters changed (shorter runs for the variants)."""
    doc = copy.deepcopy(shipped_doc())
    pid = pulse_id(doc)
    for s in doc["steps"]:
        if s["id"] == pid:
            s["params"].update(params)
    return doc, pid


SHORT = {"round_trips": 2, "from_mm": 5, "to_mm": 45}


def fill(sd: dict) -> dict:
    """Valid operator entries for a step definition (inside the limits, the expected choice)."""
    out: dict[str, Any] = {}
    for f in sd.get("fields", ()):
        k = f["kind"]
        if k == "confirm":
            out[f["name"]] = True
        elif k == "text":
            out[f["name"]] = "F walk-through"
        elif k == "choice":
            out[f["name"]] = (f.get("pass_values") or f["choices"])[0]
        else:
            lo, hi = f.get("min"), f.get("max")
            v = (lo + hi) / 2 if lo is not None and hi is not None else lo + 1 if lo is not None else \
                hi / 2 if hi is not None else 1.0
            out[f["name"]] = round(float(v), int(f.get("decimals", 2)))
    return out


def wire_info(be) -> dict:
    """GET_INFO of the connected board, from the wire (ref_codec)."""
    r = H.rx(be, "GET_INFO", kind="response")
    assert r, "no GET_INFO response on the wire"
    return r[-1].fields["info"]


def identity_doc(doc: dict, info: dict) -> dict:
    """The step file with the release identity (FW version / build) replaced by the identity the simulator reports
    on the wire — the only change of the walk-through (the simulator is not the release image)."""
    d = copy.deepcopy(doc)
    fw = ".".join(map(str, info["fw_version"]))
    for s in d["steps"]:
        a = s.get("auto") or {}
        for c in [c for st in a.get("stages", ()) for c in st["all"]] + list(a.get("fail_if", ())):
            if c["src"] == "info" and c["key"] == "fw_version" and isinstance(c.get("value"), str):
                c["value"] = fw                                   # stage `eq` and the mismatch `fail_if` (`ne`)
            if c["src"] == "info" and c["key"] == "build" and isinstance(c.get("value"), str):
                c["value"] = info["build"]
    return d


def session_json(be) -> dict:
    H.advance(be, 300)                        # lock-step: the writer runs inside the tick (§25.3)
    return json.loads((Path(H.bu_status(be).folder) / "session.json").read_text(encoding="utf-8"))


def ready(be, *, home: bool = True) -> None:
    """Driver powered + enabled, no-specimen mode, fast homing, homed (the operator's preparation of a motion step)."""
    if not H.motion(be).enabled:
        H.no_specimen(be)
        g = H.enable(be)
        assert g.ok, g
        assert H.run_until(be, lambda: H.motion(be).enabled, 3000)
    if not H.safety(be).no_specimen_mode:
        H.no_specimen(be)
    H.result(be, H.write_verify(be, {"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
    if home and not H.motion(be).homed:
        H.result(be, H.home(be, load_confirmed=True), 120_000)
        assert H.run_until(be, lambda: H.motion(be).homed and not H.motion(be).moving, 5000)


def action_end(be, sid: str, ms: float = 900_000) -> str:
    assert H.run_until(be, lambda: H.bu_step(be, sid).action_state in ("PASS", "FAIL", "ABORTED"), ms, 5.0), \
        H.bu_step(be, sid)
    return H.bu_step(be, sid).action_state


def motion_tx(be, since: int) -> list:
    return [w for w in H.tx(be, since=since) if w.name in MOTION_CMDS]


def homed_values(be, since: int) -> list[int]:
    return [int(w.fields["value"]) for w in H.events(be, since=since, code="HOMED")]


def drift_latched_on_wire(be, since: int) -> bool:
    for w in H.events(be, since=since, code="FAULT_SET"):
        a = w.fields.get("arg")
        if a == "HOME_DRIFT" or (isinstance(a, int) and 0 <= a < len(rc.FAULTS) and rc.FAULTS[a] == "HOME_DRIFT"):
            return True
    return False


def homes_issued_while_homed(be, since: int) -> list[bool]:
    """For every HOME request since ``since``: was HOMED set in the last DATA frame before it (SYS-011 validity)?"""
    out, homed = [], None
    start = max(0, since - 2000)                     # the DATA frames just before the mark count as well
    for i, w in enumerate(H.wire(be, start), start):
        if w.dir == "RX" and w.name == "DATA":
            homed = "HOMED" in w.fields.get("flags", [])
        elif w.dir == "TX" and w.name == "HOME" and i >= since:
            out.append(bool(homed))
    return out


def sys011_verdict(d0: list[int], d1: int | None, drift: bool) -> str:
    """The SRS rule, computed here."""
    if max(abs(x) for x in d0) > SYS011["baseline_max_um"]:
        return "BASELINE_SCATTER"
    limit = max(SYS011["d_floor_um"], SYS011["d_factor"] * max(abs(x) for x in d0))
    return "PASS" if not drift and d1 is not None and abs(d1) <= limit else "FAIL"


@pytest.fixture
def bub(tmp_path):
    """Factory of lock-step backends with the bring-up engine (step-file override optional); shut down at teardown."""
    made = []

    def make(steps: str | None = None, **kw):
        be = H.bu_backend(tmp_path, steps=steps, **kw)
        made.append(be)
        return be

    yield make
    for be in made:
        try:
            be.shutdown()
        except Exception:  # noqa: BLE001
            pass


# ============================================================================================ walk-through stimuli
# Keyed by the guide step ids. Each is what the operator / the world does for that step on the simulator; the
# automatic evaluation must then pass from the board indication alone.

def _hold(be, ms: float = 1500.0) -> None:
    """Hold a state long enough for the GET_STATUS poll (1 s with the stream on) and the 100 ms evaluation."""
    H.advance(be, ms)


def _estop_cycle(be) -> None:
    H.act(be, "estop", open=True)
    _hold(be)
    H.act(be, "estop", open=False)
    H.advance(be, 200)
    H.estop_clear(be)
    _hold(be)


def _power(on: bool):
    """48 V on / off: driver supply and its ALM output (open = alarm while unpowered, guide 6.8 / 7.8 — the simulator
    keeps the two inputs separate, so the world stimulus sets both)."""
    def f(be) -> None:
        H.act(be, "drv_power", on=on)
        H.act(be, "alm", active=not on)
        _hold(be)
    return f


def _reset_board(be) -> None:
    H.act(be, "reset", cause="pin")
    H.advance(be, 3000)


def _limit_cycle(name: str):
    def f(be) -> None:
        H.act(be, "limit", name=name, active=True)
        H.advance(be, 300)
        H.act(be, "limit", name=name, active=None)
        H.advance(be, 300)
    return f


def _jog(be, direction: int, speed: float, ms: float, until=None) -> None:
    """Hold a jog button for ``ms`` (GUI beats as the real GUI thread gives them), or until ``until()``."""
    H.jog_start(be, direction, speed)
    t = 0.0
    while t < ms and not (until and until()):
        H.gui_beat(be)
        H.advance(be, 20)
        t += 20
    H.jog_stop(be)
    H.advance(be, 500)


def _jog_to_switch(name: str):
    """Un-homed jog onto a switch (guide 9.5 / 9.8): the switch is placed 1.5 mm ahead of the carriage in the world,
    the jog runs until the FW stops it at the switch, then jogs back off it; the switch is put back afterwards."""
    def f(be) -> None:
        start0, end0 = H.world_switches(be)
        x = H.world_x_um(be)
        d = -1 if name == "start" else +1
        H.act(be, "limit", name=name, position_um=int(x + d * 1500))
        m = H.wire_mark(be)
        _jog(be, d, 2.0, 5000, until=lambda: bool(H.events(be, since=m, code="STOPPED")))
        _hold(be, 600)
        _jog(be, -d, 2.0, 1500)
        _hold(be, 600)
        H.act(be, "limit", name=name, position_um=start0 if name == "start" else end0)
        H.advance(be, 200)
    return f


def _unhome(be) -> None:
    H.disable(be, confirmed=True)
    H.advance(be, 400)
    H.enable(be)
    H.advance(be, 800)


def _home(be) -> None:
    ready(be, home=False)
    H.result(be, H.home(be, load_confirmed=True), 120_000)
    H.advance(be, 500)


def _seq(*fs):
    def f(be) -> None:
        for x in fs:
            x(be)
    return f


STIM = {
    "7.1": lambda be: (H.act(be, "estop", open=True), H.advance(be, 1200)),
    "7.2": lambda be: (H.act(be, "estop", open=False), H.advance(be, 200), H.estop_clear(be), H.advance(be, 300)),
    "7.3": lambda be: (H.act(be, "button", name="pause", pressed=True), H.advance(be, 200),
                       H.act(be, "button", name="pause", pressed=False), H.advance(be, 300), H.resume(be),
                       H.advance(be, 300)),
    "7.4": _limit_cycle("start"),
    "7.5": _limit_cycle("end"),
    "7.6": lambda be: [(H.act(be, "wire", input=n, broken=b), H.advance(be, 300))
                       for n in ("start", "end") for b in (True, False)],
    "7.7": lambda be: (H.act(be, "wire", input="start", broken=True), H.act(be, "wire", input="end", broken=True),
                       _hold(be), H.act(be, "wire", input="start", broken=False),
                       H.act(be, "wire", input="end", broken=False), H.advance(be, 300), H.fault_clear(be),
                       _hold(be)),
    "7.8": _power(False),
    "8.1": _reset_board,
    "8.3": _power(True),
    "8.4": _estop_cycle,
    "8.5": lambda be: (H.enable(be), H.advance(be, 800)),
    "8.6": lambda be: (H.disable(be, confirmed=True), _hold(be), H.enable(be), _hold(be)),
    "8.7": _reset_board,
    "9.1": lambda be: (H.no_specimen(be), H.advance(be, 300)),
    "9.2": lambda be: (H.enable(be), H.advance(be, 800)),
    "9.3": lambda be: _jog(be, +1, 2.0, 1000),
    "9.5": _jog_to_switch("start"),
    "9.6": _home,
    "9.8": _seq(_unhome, _jog_to_switch("end")),
    "9.9": _home,
    "10.1.2": _power(False),
    "10.1.3": _estop_cycle,
    "10.1.4": _seq(_power(True), lambda be: (H.enable(be), _hold(be, 800)),
                   lambda be: _jog(be, +1, 1.0, 1000), lambda be: _jog(be, -1, 1.0, 300), _hold),
    "10.2.1": _home,
    "10.2.7": lambda be: (be_leave_no_specimen(be), H.advance(be, 300)),
    "11.2.1": lambda be: (H.pause(be), H.advance(be, 300)),
    "11.3.3": lambda be: (H.resume(be), H.advance(be, 300)),
    "12.1": lambda be: (H.result(be, H.save_nvm(be)), H.advance(be, 1500)),
    "12.3": _seq(_power(False), _reset_board, _hold),
    "12.4": lambda be: (H.result(be, H.read_all(be)), H.advance(be, 1500)),
}


def be_leave_no_specimen(be) -> None:
    g = H.set_no_specimen(be, False)      # noqa: F841 — refusal shows in the step result


def _travel_wizard(be) -> None:
    ready(be)
    tc = H.travel_cal(be)
    g = tc.start()
    if g.needs_confirmation:
        g = tc.start(confirmed=True)
    assert g.ok, g
    ref: dict[str, float] = {}
    seen: set = set()
    for _ in range(30_000):
        st = tc.state()
        if st.phase in ("DONE", "ABORTED", "REFUSED"):
            break
        key = (st.phase, st.step_index)
        if st.can_continue and key not in seen:
            seen.add(key)
            inputs = None
            if st.phase == "REFERENCE":
                ref["x0"] = H.world_x_um(be) / 1000.0           # the caliper is zeroed here (world truth)
            elif st.phase in ("ENTER_D1", "ENTER_DTOT"):
                d = round(H.world_x_um(be) / 1000.0 - ref["x0"], 2)
                inputs = {sp.key: d for sp in st.inputs}
            tc.continue_(inputs, confirmed=st.needs_confirmation is not None)
        elif st.can_continue and st.needs_confirmation is not None:
            tc.continue_(None, confirmed=True)
        H.advance(be, 20)
    assert tc.state().phase == "DONE", tc.state()


def _load_wizard(be) -> None:
    _fit, done = H.load_calibration(be)
    assert done.phase == "DONE", done


def _tare(be) -> None:
    t = H.tare(be)
    assert getattr(t, "phase", None) == "DONE", t


WIZARD = {"travel_wizard": _travel_wizard, "load_wizard": _load_wizard, "tare": _tare}


def _evidence(be, sd: dict, since: int) -> list[str]:
    """Board evidence on the wire (ref_codec) for the conditions of a passed automatic step; returns what is missing."""
    missing = []
    w_all = None
    for st in (sd.get("auto") or {}).get("stages", ()):
        for c in st["all"]:
            src, key, op = c["src"], c["key"], c["op"]
            if src == "event" and op == "seen":
                if not H.events(be, since=since, code=key):
                    missing.append(f"EVENT {key}")
            elif src in ("data_flag", "data_status") and op in ("set", "clear"):
                w_all = w_all if w_all is not None else H.rx(be, "DATA", since=since)
                fld = "flags" if src == "data_flag" else "status"
                want = op == "set"
                if not any((key in w.fields.get(fld, [])) == want for w in w_all):
                    missing.append(f"DATA {fld} {key} {op}")
            elif src in ("fault", "io", "sys_flag") and op in ("set", "clear"):
                fld = {"fault": "faults", "io": "io", "sys_flag": "sys_flags"}[src]
                st_all = [w.fields["board_status"] for w in H.rx(be, "GET_STATUS", kind="response")
                          if "board_status" in w.fields]
                want = op == "set"
                if not any((key in b.get(fld, [])) == want for b in st_all):
                    missing.append(f"STATUS {fld} {key} {op}")
    return missing


def walk(be, doc: dict, *, note=print) -> dict[str, tuple[str, str]]:
    """Walk every step in order; returns {id: (state, text)}. Raises on a missing stimulus for an automatic step."""
    out: dict[str, tuple[str, str]] = {}
    for sd in doc["steps"]:
        sid = sd["id"]
        m0 = H.wire_mark(be)
        H.bu_select(be, sid)
        H.advance(be, 150)
        st = H.bu_step(be, sid)
        assert st.instruction.strip() and st.expected.strip(), f"{sid}: instruction / expected missing"
        stages = (sd.get("auto") or {}).get("stages", ())
        needs_event = bool(stages) and any(c["src"] == "event" and c["op"] == "seen" for c in stages[0]["all"])
        if needs_event:                                   # never passes before its board evidence
            assert st.state == "NOT_DONE", f"{sid}: {st.state} before the stimulus ({st.auto})"
        f = STIM.get(sid)
        if sd.get("auto") and f is None and sd["kind"] != "info":
            note(f"{sid}: no stimulus (condition expected to hold already)")
        if f is not None:
            f(be)
        if sd["kind"] == "pulse_integrity":
            ready(be)
            g = H.bu_run(be, sid)
            assert g.needs_confirmation and "BRINGUP_MOTION" in g.codes(), g
            assert H.bu_run(be, sid, confirmed=True).ok
            action_end(be, sid)
        elif sd.get("action") in WIZARD:
            WIZARD[sd["action"]](be)
        if sd.get("fields"):
            iss = H.bu_submit(be, sid, fill(sd))
            assert not [i for i in iss if str(getattr(i.severity, "value", i.severity)).upper() == "ERROR"], iss
        if sd["kind"] == "info":
            assert H.bu_mark(be, sid, "PASS", remark="F walk-through").ok
        H.run_until(be, lambda sid=sid: H.bu_step(be, sid).state in ("PASS", "FAIL"),
                    5000 if (sd.get("auto") or sd["kind"] in ("wizard", "pulse_integrity")) else 400, 5.0)
        s = H.bu_step(be, sid)
        text = s.auto.text if s.auto is not None else ""
        if s.state == "PASS" and sd.get("auto"):
            miss = _evidence(be, sd, m0 if needs_event else 0)
            assert not miss, f"{sid}: PASS without board evidence on the wire: {miss}"
        out[sid] = (s.state, text)
    return out


# ============================================================================================ SW-BRU-001

@pytest.mark.req("SW-BRU-001")
def test_tc_sw_bru_001_01_step_ids_are_the_guide_ids():
    """TC-SW-BRU-001-01 (ids): the shipped step file names the steps of the delivered guide (parsed here)."""
    doc = shipped_doc()
    gids = guide_ids(guide_path().read_text(encoding="utf-8"))
    fids = [s["id"] for s in doc["steps"]]
    assert len(fids) == len(set(fids))
    assert not set(fids) - set(gids), f"step ids not in the guide: {sorted(set(fids) - set(gids))}"
    if is_example(doc):
        pytest.fail(f"shipped step file is B's example ({len(fids)} of {len(gids)} guide steps) — A's content "
                    "missing (OI-A-BRU-01)")
    assert fids == gids, f"missing {sorted(set(gids) - set(fids))}; order differs: {fids != [g for g in gids if g in fids]}"
    sec = {s["id"] for s in doc["sections"]}
    assert {g.split(".")[0].split("-")[0] for g in gids} <= sec


@pytest.mark.req("SW-BRU-001", "SW-BRU-002", "SW-BRU-003", "SYS-011")
def test_tc_sw_bru_001_01_walkthrough_every_step_on_the_simulator(bub, tmp_path):
    """TC-SW-BRU-001-01: every step of the shipped file, in order, on the lock-step simulator: instruction + expected
    shown, not done → PASS (automatic steps from their board evidence on the wire, entry steps from valid entries,
    the pulse-integrity check with the shipped parameters); the session ends complete and packs."""
    probe = bub()
    info = wire_info(probe)
    probe.shutdown()
    doc = identity_doc(shipped_doc(), info)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    st0 = H.bu_status(be)
    assert st0.active and st0.total == len(doc["steps"]) and st0.done == 0
    notes: list[str] = []
    res = walk(be, doc, note=notes.append)
    bad = {k: v for k, v in res.items() if v[0] != "PASS"}
    assert not bad, "\n".join(f"{k}: {v[0]} {v[1][:160]}" for k, v in bad.items())
    st = H.bu_status(be)
    assert (st.done, st.passed, st.failed, st.skipped) == (st.total, st.total, 0, 0), st
    pid = pulse_id(doc)
    r = session_json(be)["steps"][pid]["action"]["result"]
    assert r["round_trips"] == SYS011["round_trips"] and r["moves_done"] == 2 * SYS011["round_trips"]
    assert H.bu_close(be).ok
    pkg = H.bu_package(be)
    man, members = rehash_zip(pkg.path)
    assert man["partial"] is False and pkg.partial is False
    rec = json.loads(members["bringup.json"])
    want = {"total": len(doc["steps"]), "pass": len(doc["steps"]), "fail": 0, "skipped": 0, "not_done": 0}
    assert {k: rec["summary"].get(k) for k in want} == want, rec["summary"]


@pytest.mark.req("SW-BRU-001")
def test_tc_sw_bru_001_02_latch_and_status_bit_follow_the_wire(bub, tmp_path):
    """TC-SW-BRU-001-02 (latch, status bit): pass and fail stimuli; verdict = the board indication on the wire."""
    be = bub()
    assert H.bu_start(be).ok
    # E-stop latch (7.1): no PASS without ESTOP_SET; FAIL when another fault latches while it waits
    H.bu_select(be, "7.1")
    H.advance(be, 1500)
    assert H.bu_step(be, "7.1").state == "NOT_DONE"
    m = H.wire_mark(be)
    H.act(be, "estop", open=True)
    assert H.run_until(be, lambda: H.bu_step(be, "7.1").state == "PASS", 3000)
    assert H.events(be, since=m, code="ESTOP_SET")
    _estop_cycle(be)
    assert H.bu_mark(be, "7.1", "NOT_DONE").ok
    H.bu_select(be, "6.9")
    H.bu_select(be, "7.1")
    m = H.wire_mark(be)
    H.act(be, "wire", input="start", broken=True)
    H.act(be, "wire", input="end", broken=True)                        # LIMIT_WIRING → FAULT_SET
    assert H.run_until(be, lambda: H.bu_step(be, "7.1").state == "FAIL", 3000), H.bu_step(be, "7.1")
    assert H.events(be, since=m, code="FAULT_SET")
    assert "fault" in H.bu_step(be, "7.1").auto.text.lower()
    H.act(be, "wire", input="start", broken=False)
    H.act(be, "wire", input="end", broken=False)
    H.advance(be, 300)
    H.fault_clear(be)
    H.advance(be, 500)
    # START limit status bit (7.4): two stages, each from a DATA frame
    H.bu_select(be, "7.4")
    m = H.wire_mark(be)
    H.act(be, "limit", name="start", active=True)
    H.advance(be, 400)
    s = H.bu_step(be, "7.4")
    assert s.state == "NOT_DONE" and s.auto.state == "WAITING"           # stage 2 (release) not yet
    assert any("LIMIT_START" in w.fields["status"] for w in H.rx(be, "DATA", since=m))
    H.act(be, "limit", name="start", active=None)
    assert H.run_until(be, lambda: H.bu_step(be, "7.4").state == "PASS", 3000)


@pytest.mark.req("SW-BRU-001")
def test_tc_sw_bru_001_02_motion_result_home_pass_and_home_failed(bub):
    """TC-SW-BRU-001-02 (motion result): 9.6 passes on EVENT HOMED + HOMED flag; a stopped homing (HOME_FAILED on
    the wire) fails it with the observed reason."""
    be = bub()
    assert H.bu_start(be).ok
    ready(be, home=False)
    H.bu_select(be, "9.6")
    m = H.wire_mark(be)
    fut = H.home(be, load_confirmed=True)
    H.advance(be, 300)
    H.stop(be)                                                          # any stop during homing → HOME_FAILED
    H.advance(be, 1500)
    assert H.events(be, since=m, code="HOME_FAILED"), [w.fields for w in H.events(be, since=m)]
    assert H.run_until(be, lambda: H.bu_step(be, "9.6").state == "FAIL", 3000), H.bu_step(be, "9.6")
    assert "hom" in H.bu_step(be, "9.6").auto.text.lower()
    assert H.bu_mark(be, "9.6", "NOT_DONE").ok
    m = H.wire_mark(be)
    H.result(be, H.home(be, load_confirmed=True), 120_000)
    assert H.run_until(be, lambda: H.bu_step(be, "9.6").state == "PASS", 3000)
    assert H.events(be, since=m, code="HOMED")
    del fut


@pytest.mark.req("SW-BRU-001")
def test_tc_sw_bru_001_02_nvm_save_and_verify(bub):
    """TC-SW-BRU-001-02 (NVM): Save to NVM passes on PARAMS_SAVED (wire) + CFG clean; nothing before the save."""
    be = bub()
    assert H.bu_start(be).ok
    H.bu_select(be, "12.1")
    H.advance(be, 1500)
    assert H.bu_step(be, "12.1").state == "NOT_DONE"
    m = H.wire_mark(be)
    H.result(be, H.save_nvm(be))
    assert H.run_until(be, lambda: H.bu_step(be, "12.1").state == "PASS", 3000), H.bu_step(be, "12.1")
    assert H.events(be, since=m, code="PARAMS_SAVED") and H.tx(be, "SAVE_PARAMS", since=m)


@pytest.mark.req("SW-BRU-001")
@pytest.mark.defect("SWD-P3-02")
def test_tc_sw_bru_001_02_no_evaluation_without_the_link(bub, tmp_path):
    """TC-SW-BRU-001-02: with the link down an automatic step is not evaluated — a condition on DATA bits must not
    pass on the last (or zeroed) bits after the link is gone (SWD-P3-02: found in the hand-back version, fixed by B
    in the same round, B-BRU-04 item 7 — kept as the regression test)."""
    doc = copy.deepcopy(shipped_doc())
    doc["steps"].append({"id": "13.98", "section": doc["sections"][-1]["id"], "title": "F probe: released",
                         "instruction": "x", "expected": "PAUSED clear, START clear",
                         "kind": "auto", "auto": {"stages": [{"all": [
                             {"src": "data_status", "key": "PAUSED", "op": "clear"},
                             {"src": "data_status", "key": "LIMIT_START", "op": "clear"}]}]}})
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    for sid in ("6.5", "6.7", "6.8"):                                  # board values: not without the link
        if sid in [s["id"] for s in doc["steps"]]:
            H.disconnect(be)
            H.advance(be, 500)
            assert H.link_state(be) != "CONNECTED"
            H.bu_select(be, sid)
            H.advance(be, 1500)
            assert H.bu_step(be, sid).state == "NOT_DONE", (sid, H.bu_step(be, sid).auto)
            H.connect(be)
            H.advance(be, 500)
            H.bu_select(be, "13.98")
    H.act(be, "limit", name="start", active=True)
    H.advance(be, 300)
    H.disconnect(be)
    H.advance(be, 500)
    H.bu_select(be, "13.98")
    H.advance(be, 1500)
    s = H.bu_step(be, "13.98")
    assert s.state == "NOT_DONE", f"PASS with the link {H.link_state(be)}: {s.auto}"


@pytest.mark.req("SW-BRU-001")
@pytest.mark.defect("SWD-P3-03")
def test_tc_sw_bru_001_02_identity_and_parameter_mismatch_fail_with_the_observed_value(bub):
    """TC-SW-BRU-001-02 (GET_INFO identity, parameter read-back): another FW build / dictionary than expected, or a
    board value other than expected, ends the step FAIL with the observed value — not an endless "waiting"
    (SWD-P3-03)."""
    be = bub()
    assert H.bu_start(be).ok
    info = wire_info(be)
    assert info["build"] != "20261010-2135"                              # the simulator is not the release image
    H.bu_select(be, "6.3")
    H.advance(be, 3000)
    s = H.bu_step(be, "6.3")
    assert s.state == "FAIL" and info["build"] in s.auto.text, s


@pytest.mark.req("SW-BRU-001")
def test_tc_sw_bru_001_03_entries_limits_units_skip(bub):
    """TC-SW-BRU-001-03 (API part; the form widgets are D's GUI tests): measured value with unit and limits —
    below / at / inside / above, non-number, empty; choice not offered; confirmation; skip needs a reason."""
    doc = shipped_doc()
    be = bub()
    g = H.bu_submit(be, doc["steps"][0]["id"], fill(doc["steps"][0]))
    assert g and "NO_SESSION" in [i.code for i in g]                     # nothing before a session
    assert H.bu_start(be).ok
    sd = next(s for s in doc["steps"] if any(f["kind"] == "number" and f.get("min") is not None
                                              and f.get("max") is not None and f.get("unit")
                                              for f in s.get("fields", ())))
    sid = sd["id"]
    f = next(f for f in sd["fields"] if f["kind"] == "number" and f.get("min") is not None
             and f.get("max") is not None)
    base = fill(sd)
    lo, hi, unit = f["min"], f["max"], f.get("unit", "")
    for v, want in ((lo, "PASS"), (hi, "PASS"), ((lo + hi) / 2, "PASS"), (lo - abs(lo) * 0.02 - 0.01, "FAIL"),
                    (hi + abs(hi) * 0.02 + 0.01, "FAIL")):
        iss = H.bu_submit(be, sid, {**base, f["name"]: v})
        assert H.bu_step(be, sid).state == want, (v, iss)
        if want == "FAIL":
            txt = " ".join(i.text for i in iss)
            assert f"{v:g}" in txt and unit in txt, txt                 # the value and its unit in the result
            assert H.bu_step(be, sid).entries[f["name"]] == v           # the failing value is kept
    good = dict(H.bu_step(be, sid).entries)
    for bad in ("abc", "", None, "1.0 V"):
        iss = H.bu_submit(be, sid, {**base, f["name"]: bad})
        assert iss and any(str(i.severity.value).upper() == "ERROR" for i in iss), (bad, iss)
        assert dict(H.bu_step(be, sid).entries) == good                 # nothing stored
    ch = next((s for s in doc["steps"] if any(x["kind"] == "choice" for x in s.get("fields", ()))), None)
    if ch is not None:
        cf = next(x for x in ch["fields"] if x["kind"] == "choice")
        iss = H.bu_submit(be, ch["id"], {**fill(ch), cf["name"]: "not offered"})
        assert any(i.code == "NOT_A_CHOICE" for i in iss)
    cs = next(s for s in doc["steps"] if any(x["kind"] == "confirm" for x in s.get("fields", ())))
    cf = next(x for x in cs["fields"] if x["kind"] == "confirm")
    iss = H.bu_submit(be, cs["id"], {**fill(cs), cf["name"]: False})
    assert any(i.code == "REQUIRED" for i in iss) and H.bu_step(be, cs["id"]).state == "NOT_DONE"
    iss = H.bu_submit(be, cs["id"], {**fill(cs), cf["name"]: "yes"})   # only True confirms
    assert H.bu_step(be, cs["id"]).state == "NOT_DONE", iss
    g = H.bu_mark(be, cs["id"], "SKIPPED")
    assert not g.ok and "REASON_REQUIRED" in g.codes()
    assert H.bu_mark(be, cs["id"], "SKIPPED", reason="F: not applicable").ok
    s = H.bu_step(be, cs["id"])
    assert s.state == "SKIPPED" and s.skip_reason == "F: not applicable" and s.manual
    iss = H.bu_submit(be, sid, base, remark="F remark <b>")
    assert H.bu_step(be, sid).remark == "F remark <b>"


def _bad_docs() -> dict[str, dict]:
    base = shipped_doc()

    def mut(fn):
        d = copy.deepcopy(base)
        fn(d)
        return d
    out = {
        "unknown_kind": mut(lambda d: d["steps"][0].update(kind="teleport")),
        "duplicate_id": mut(lambda d: d["steps"].append(copy.deepcopy(d["steps"][0]))),
        "missing_expected": mut(lambda d: d["steps"][0].pop("expected")),
        "schema_version_2": mut(lambda d: d.update(schema_version=2)),
        "other_schema": mut(lambda d: d.update(schema="bird.bend.sequence")),
        "unknown_section": mut(lambda d: d["steps"][0].update(section="99")),
        "unknown_action": mut(lambda d: d["steps"][0].update(action="launch")),
        "unknown_condition_key": mut(lambda d: next(s for s in d["steps"] if s.get("auto"))["auto"]["stages"][0]
                                     ["all"].append({"src": "data_flag", "key": "WARP", "op": "set"})),
        "pulse_param_missing": mut(lambda d: next(s for s in d["steps"] if s["kind"] == "pulse_integrity")
                                   ["params"].pop("d_floor_um")),
    }
    d = copy.deepcopy(base)
    for s in d["steps"]:
        for f in s.get("fields", ()):
            if f.get("min") is not None and f.get("max") is not None:
                f["min"], f["max"] = f["max"] + 1, f["min"]
                break
        else:
            continue
        break
    out["min_gt_max"] = d
    return out


@pytest.mark.req("SW-BRU-001")
@pytest.mark.parametrize("case", ["unknown_kind", "duplicate_id", "missing_expected", "min_gt_max",
                                  "schema_version_2", "other_schema", "unknown_section", "unknown_action",
                                  "unknown_condition_key", "pulse_param_missing", "not_json", "deep_nesting",
                                  "oversized", "empty", "missing_file"])
def test_tc_sw_bru_001_04_bad_step_file_refuses_the_session_not_the_app(bub, tmp_path, case):
    """TC-SW-BRU-001-04: a bad step file → ``start()`` refused STEP_FILE_INVALID naming the problem; the application
    keeps running (link, status, motion API untouched)."""
    p = tmp_path / "bad_steps.json"
    if case in ("not_json", "deep_nesting", "oversized", "empty"):
        text = {"not_json": "{steps: [", "deep_nesting": "[" * 100_000 + "]" * 100_000,
                "oversized": json.dumps({**shipped_doc(), "pad": "x" * (21 * 1024 * 1024)}), "empty": ""}[case]
        p.write_text(text, encoding="utf-8")
    elif case != "missing_file":
        p.write_text(json.dumps(_bad_docs()[case]), encoding="utf-8")
    be = bub(steps=str(p))
    assert H.link_state(be) == "CONNECTED" and H.status(be).stream.on
    g = H.bu_start(be, confirmed=True)
    assert not g.ok and g.codes() == ("STEP_FILE_INVALID",), g
    assert len(g.text()) > 30 and "not usable" in g.text()
    assert not H.bu_status(be).active and H.bu_steps(be) == []
    assert H.motion_check(be, "HOME") is not None                       # backend alive
    H.advance(be, 500)
    assert H.link_state(be) == "CONNECTED"


@pytest.mark.req("SW-BRU-001")
def test_tc_sw_bru_001_04_shipped_files_validate_with_f_validator():
    """TC-SW-BRU-001-04: the shipped step file validates against its shipped schema (F's validator)."""
    schema = json.loads(STEPS_SCHEMA.read_text(encoding="utf-8"))
    errs = f_validate(shipped_doc(), schema)
    assert not errs, errs[:10]
    rs = json.loads(RECORD_SCHEMA.read_text(encoding="utf-8"))
    assert not [e for e in f_validate({}, rs) if "not implemented" in e]


# ============================================================================================ SW-BRU-002

def _scripted_session(be, tmp_path: Path) -> dict:
    """Session of TC-SW-BRU-002-01: entries, an E-stop, two Save to NVM, a recorded motion step (HOME), a pulse
    check (short), a load calibration + tare (calibration files), an app settings file and a session preset."""
    from bend_stand.core.logfile import attach_incident_log, setup_logging  # noqa: PLC0415 — as the app (SWR-14)

    data = Path(os.environ["BEND_STAND_DATA_DIR"])
    data.mkdir(parents=True, exist_ok=True)
    setup_logging("INFO", data_dir=data)
    attach_incident_log(be.events)
    (data / "gui.ini").write_text("[General]\nf=1\n", encoding="utf-8")
    m0 = H.wire_mark(be)
    assert H.bu_start(be).ok
    doc = shipped_doc()
    meas = next(s for s in doc["steps"] if s["kind"] == "measure")
    H.bu_select(be, meas["id"])
    assert not H.bu_submit(be, meas["id"], fill(meas))
    H.bu_select(be, "7.1")
    H.act(be, "estop", open=True)
    H.advance(be, 800)
    _estop_cycle(be)
    H.result(be, H.save_nvm(be))
    H.advance(be, 1500)
    H.bu_select(be, "9.6")
    _home(be)
    H.advance(be, 1500)
    H.result(be, H.save_nvm(be))
    H.advance(be, 1500)
    H.calibrate_and_tare(be)
    H.bu_select(be, "13.1")
    H.advance(be, 1500)
    return {"m0": m0, "meas": meas}


@pytest.mark.req("SW-BRU-002")
def test_tc_sw_bru_002_01_collection_checklist(bub, tmp_path):
    """TC-SW-BRU-002-01: collected without operator action — events (count vs the wire), GET_INFO, parameter sets at
    start / each NVM save / end (values = the board's on the wire), NVM state, link statistics, incident log, app
    settings, calibration files, a recording of the motion step; all in the package."""
    from bend_stand.core.logfile import shutdown_logging  # noqa: PLC0415

    be = bub()
    try:
        _collection_checklist(be, tmp_path)
    finally:
        shutdown_logging()


def _collection_checklist(be, tmp_path) -> None:
    ctx = _scripted_session(be, tmp_path)
    st = H.bu_status(be)
    folder = Path(st.folder)
    assert H.bu_close(be).ok
    H.advance(be, 500)
    # events: every FW EVENT on the wire since the session start is in events.jsonl (fw.event), none invented
    lines = [json.loads(x) for x in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines() if x]
    fw = [x for x in lines if x["topic"] == "fw.event"]
    wire_ev = [w.fields["code"] for w in H.events(be, since=ctx["m0"])]
    names = [x["payload"].get("name") for x in fw]
    for code in set(wire_ev):
        assert names.count(code) >= wire_ev.count(code) - 1, (code, names.count(code), wire_ev.count(code))
    assert {"ESTOP_SET", "ESTOP_CLEARED", "PARAMS_SAVED", "HOMED"} <= set(names)
    assert any(x["topic"] == "bringup.data_bits" for x in lines) and any(x["topic"] == "bringup.status" for x in lines)
    # GET_INFO
    info = json.loads((folder / "info.json").read_text(encoding="utf-8"))
    wi = wire_info(be)
    assert info["info"]["uid"] == wi["uid"] and info["info"]["build"] == wi["build"]
    # parameter sets: start, 2 × nvm_save, end — values equal to the board's last read-back on the wire
    ses = json.loads((folder / "session.json").read_text(encoding="utf-8"))
    tags = [d["tag"] for d in ses["param_dumps"]]
    assert tags[0] == "start" and tags.count("nvm_save") >= 2 and tags[-1] == "end", tags
    end = json.loads((folder / ses["param_dumps"][-1]["file"]).read_text(encoding="utf-8"))
    assert end["count"] >= 40 and end["board_uid"] == wi["uid"]
    sp = end["values"]["motion.steps_per_mm"]
    assert sp == pytest.approx(H.config_values(be)["motion.steps_per_mm"])
    # NVM state and link statistics snapshots
    snaps = [json.loads(x) for x in (folder / "snapshots.jsonl").read_text(encoding="utf-8").splitlines() if x]
    kinds = {s["payload"]["kind"] for s in snaps}
    assert {"link_stats", "nvm"} <= kinds
    # one recording of the motion step (HOME of 9.6), a real recording folder with data
    recs = ses["recordings"]
    assert any(r["step"] == "9.6" for r in recs), recs
    rfold = Path(next(r["folder"] for r in recs if r["step"] == "9.6"))
    hdr, rows = H.data_rows(str(rfold))
    assert rows, "recording of the motion step is empty"
    # package: incident log, settings, calibration files, recording, session files
    pkg = H.bu_package(be)
    _man, members = rehash_zip(pkg.path)
    names_z = set(members)
    assert any(n.startswith("logs/") for n in names_z), sorted(names_z)
    assert "settings/gui.ini" in names_z
    assert any(n.startswith("calibration/") for n in names_z)
    assert any(n.startswith(f"recordings/{rfold.name}/") for n in names_z)
    for need in ("session/session.json", "session/events.jsonl", "session/info.json", "session/snapshots.jsonl",
                 "session/steps_used.json", "session/settings_start.json"):
        assert need in names_z, need
    assert sum(1 for n in names_z if n.startswith("session/params/")) == len(ses["param_dumps"])   # closed: no new dump


def _child(tmp_path: Path, phase: str, steps: str, data: Path, marker: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["BEND_STAND_DATA_DIR"] = str(data)
    env["BEND_STAND_BRINGUP_DIR"] = str(tmp_path / "pk")
    env["BEND_STAND_HOTKEY"] = "off"
    env["PYTHONPATH"] = os.pathsep.join([str(SW / "src"), str(Path(__file__).parent), env.get("PYTHONPATH", "")])
    p = subprocess.Popen([sys.executable, str(CHILD), phase, steps, str(marker), str(tmp_path / "rec")],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(tmp_path))
    H.log_process("bru_child", p.pid, f"started ({phase})")
    return p


def _kill(p: subprocess.Popen, phase: str) -> None:
    p.kill()                                         # TerminateProcess on this child's own handle (its PID)
    p.wait(15)
    H.log_process("bru_child", p.pid, f"killed ({phase}) rc={p.returncode}")


def _wait_marker(p: subprocess.Popen, marker: Path, timeout_s: float = 120.0) -> dict:
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        if marker.is_file():
            try:
                return json.loads(marker.read_text(encoding="utf-8"))
            except ValueError:
                pass
        if p.poll() is not None:
            out = p.stdout.read().decode(errors="replace") if p.stdout else ""
            raise AssertionError(f"child ended early rc={p.returncode}: {out[-3000:]}")
        time.sleep(0.05)
    raise AssertionError("child marker not written")


@pytest.mark.req("SW-BRU-002", "SW-BRU-004")
@pytest.mark.parametrize("phase", ["entry", "auto", "motion", "save", "package"])
def test_tc_sw_bru_002_02_kill_and_restart_resumes(bub, tmp_path, monkeypatch, phase):
    """TC-SW-BRU-002-02: the session runs in a separate process (real clock, the application's backend + the
    simulator); it is killed by PID at five points and resumed here: committed entries / results are all present,
    none duplicated; an interrupted motion step is shown interrupted and is never re-started (wire)."""
    doc, _pid = pulse_doc(round_trips=3, from_mm=5, to_mm=145)
    steps = write_doc(tmp_path, doc)
    data = tmp_path / "appdata-child"
    marker = tmp_path / "marker.json"
    p = _child(tmp_path, phase, steps, data, marker)
    try:
        mk = _wait_marker(p, marker)
    finally:
        if p.poll() is None:
            _kill(p, phase)
    monkeypatch.setenv("BEND_STAND_DATA_DIR", str(data))
    be = bub(steps=steps)
    m0 = H.wire_mark(be)
    st = H.bu_status(be)
    assert st.active and st.session_id == mk["session_id"], (st, mk)
    for sid, e in mk["entries"].items():                                 # committed before the kill → present
        s = H.bu_step(be, sid)
        assert dict(s.entries) == e and s.state == mk["states"][sid], (sid, s)
    ses = session_json(be)
    for sid in mk["entries"]:
        assert sum(1 for h in ses["steps"][sid]["history"] if h["what"] == "ENTRIES") == 1, ses["steps"][sid]
    for sid in mk.get("marked", ()):
        assert H.bu_step(be, sid).state == "PASS" and H.bu_step(be, sid).manual
    if phase == "auto":
        s = H.bu_step(be, mk["auto_step"])
        assert s.state == "NOT_DONE" and s.auto.state != "PASS"
    if phase == "motion":
        s = H.bu_step(be, mk["pulse"])
        assert s.action_state == "ABORTED" and "interrupted" in s.action_text and s.state == "NOT_DONE", s
    if phase == "save":
        for d in ses["param_dumps"]:
            json.loads((Path(st.folder) / d["file"]).read_text(encoding="utf-8"))
    if phase == "package":
        for z in (tmp_path / "pk").glob("*.zip"):
            rehash_zip(z)                                                # a finished zip is complete or absent
    H.advance(be, 3000)
    assert not motion_tx(be, m0), [w.name for w in motion_tx(be, m0)]   # nothing re-started


@pytest.mark.req("SW-BRU-002")
@pytest.mark.defect("SWD-P3-04")
def test_tc_sw_bru_002_03_other_board_uid_is_flagged(bub, monkeypatch):
    """TC-SW-BRU-002-03 (other UID): a resumed session that meets another board must not mix silently: the PO sees
    both boards in the record (HTML) and the assistant shows the change (SWD-P3-04)."""
    import bend_stand.io.sim.board as simboard  # noqa: PLC0415 — the world: another board on the same port

    be = bub()
    assert H.bu_start(be).ok
    uid1 = H.bu_status(be).board_uid
    be.shutdown()
    monkeypatch.setattr(simboard, "SIM_UID", "0123456789ABCDEF01234567")
    be2 = bub()
    st = H.bu_status(be2)
    assert st.active
    ses = session_json(be2)
    assert [b["uid"] for b in ses["boards_seen"]] == [uid1, "0123456789ABCDEF01234567"]
    pkg = H.bu_package(be2)
    _m, members = rehash_zip(pkg.path)
    html_text = members["report.html"].decode("utf-8")
    assert "0123456789ABCDEF01234567" in html_text, "the second board is invisible in the report"
    st = H.bu_status(be2)
    assert st.board_mismatch and "0123456789ABCDEF01234567" in str(st.board_mismatch), st     # shown to the operator
    doc = shipped_doc()
    sd = next(x for x in doc["steps"] if x["kind"] == "confirm")
    before = H.bu_step(be2, sd["id"]).state
    iss = H.bu_submit(be2, sd["id"], fill(sd))
    g = H.bu_mark(be2, sd["id"], "PASS")
    assert iss and not g.ok and H.bu_step(be2, sd["id"]).state == before, (iss, g)          # no mixing of results


_SWD08 = [pytest.mark.defect("SWD-P3-08")]                # fixed by B (SESSION_UNREADABLE)


@pytest.mark.req("SW-BRU-002")
@pytest.mark.parametrize("damage", ["truncated", "not_an_object", pytest.param("steps_wrong_type", marks=_SWD08),
                                    pytest.param("step_record_wrong_type", marks=_SWD08)])
def test_tc_sw_bru_002_03_corrupt_session_file(bub, caplog, damage):
    """TC-SW-BRU-002-03 (corrupt session file): never a crash and never a silent empty session — the backend starts,
    the damaged session is not resumed, the error is logged, its folder is kept and ``resume`` names it."""
    be = bub()
    assert H.bu_start(be).ok
    st = H.bu_status(be)
    H.bu_submit(be, next(s["id"] for s in shipped_doc()["steps"] if s.get("fields")),
                fill(next(s for s in shipped_doc()["steps"] if s.get("fields"))))
    be.shutdown()
    f = Path(st.folder) / "session.json"
    text = f.read_text(encoding="utf-8")
    d = json.loads(text)
    if damage == "truncated":
        f.write_text(text[: len(text) // 2], encoding="utf-8")
    elif damage == "not_an_object":
        f.write_text("[1, 2, 3]", encoding="utf-8")
    elif damage == "steps_wrong_type":
        d["steps"] = "garbage"
        f.write_text(json.dumps(d), encoding="utf-8")
    else:
        first = next(iter(d["steps"]))
        d["steps"][first] = ["not", "a", "record"]
        f.write_text(json.dumps(d), encoding="utf-8")
    be2 = bub()
    assert H.link_state(be2) == "CONNECTED"
    st2 = H.bu_status(be2)
    assert not (st2.active and st2.done == 0 and st2.session_id == st.session_id), "silent empty session"
    assert f.is_file() and Path(st.folder).is_dir()
    if not st2.active:
        g = H.bu_resume(be2, st.session_id)
        assert not g.ok and st.session_id in g.text() or "not readable" in g.text(), g
        assert any(r.levelname == "ERROR" and "bring-up session" in r.getMessage() for r in caplog.records)
    assert H.bu_start(be2, confirmed=True).ok                            # a new session is still possible


# ============================================================================================ SW-BRU-003

def _complete_small_session(be) -> dict:
    """A session with entries (incl. operator text with markup), a skip, a fail and a mark; returns what was set."""
    doc = shipped_doc()
    assert H.bu_start(be).ok
    meas = [s for s in doc["steps"] if s["kind"] == "measure" and any(f.get("min") is not None for f in s["fields"])]
    a, b = meas[0], meas[1]
    ea = fill(a)
    txt = next((f["name"] for s in doc["steps"] for f in s.get("fields", ()) if f["kind"] == "text"), None)
    H.bu_submit(be, a["id"], ea, remark="<script>alert(1)</script> & \"q\"")
    fb = fill(b)
    nf = next(f for f in b["fields"] if f["kind"] == "number" and f.get("min") is not None)
    fb[nf["name"]] = nf["min"] - 1
    H.bu_submit(be, b["id"], fb)
    skip = next(s for s in doc["steps"] if s["kind"] == "confirm")
    H.bu_mark(be, skip["id"], "SKIPPED", reason="F <i>skip</i>")
    out = {"a": (a["id"], ea), "b": (b["id"], fb), "skip": skip["id"]}
    if txt is not None:
        ts = next(s for s in doc["steps"] if any(f["kind"] == "text" for f in s.get("fields", ())))
        e = fill(ts)
        e[txt] = "<img src=x onerror=alert(2)>"
        H.bu_submit(be, ts["id"], e)
        out["t"] = (ts["id"], e)
    return out


@pytest.mark.req("SW-BRU-003")
def test_tc_sw_bru_003_01_package_name_manifest_rehash(bub):
    """TC-SW-BRU-003-01: one zip, name = date + board UID + FW build; every member except the manifest listed with a
    SHA-256 that F recomputes; no unlisted / missing member; versions app / ICD / FW / OS."""
    be = bub()
    _complete_small_session(be)
    wi = wire_info(be)
    pkg = H.bu_package(be)
    p = Path(pkg.path)
    created = H.bu_status(be).started_utc[:10].replace("-", "")
    assert re.fullmatch(rf"bringup_\d{{8}}_{re.escape(wi['uid'])}_{re.escape(wi['build'])}(_\d+)?\.zip", p.name), p.name
    assert p.name[8:16] >= created
    assert pkg.size_bytes == p.stat().st_size and pkg.sha256 == hashlib.sha256(p.read_bytes()).hexdigest()
    man, members = rehash_zip(p)
    assert pkg.files == len(members)
    for n in members:
        assert ".." not in n.split("/") and not n.startswith("/") and ":" not in n and "\\" not in n, n
    rec = json.loads(members["bringup.json"])
    v = rec["versions"]
    assert v["icd_version"] == rc.ICD_VERSION
    assert v["fw_build"] == wi["build"] and v["board_uid"] == wi["uid"]
    assert v["fw_version"] == ".".join(map(str, wi["fw_version"]))
    assert v["os"] == platform.platform() and v["python"] == sys.version.split()[0]
    pyproj = (SW / "pyproject.toml").read_text(encoding="utf-8")
    assert v["sw_version"].split("+")[0] == re.search(r'^version\s*=\s*"([^"]+)"', pyproj, re.M).group(1)
    assert pkg.send_to and "product owner" in pkg.send_to.lower()
    assert members["bringup_record.schema.json"] == RECORD_SCHEMA.read_bytes()


@pytest.mark.req("SW-BRU-003")
def test_tc_sw_bru_003_02_json_schema_html_escaping(bub):
    """TC-SW-BRU-003-02: bringup.json validates against the published schema (F's validator); the HTML parses, lists
    every step with its state / entries / automatic result, carries no script or external resource, escapes every
    operator text."""
    be = bub()
    put = _complete_small_session(be)
    H.bu_select(be, "6.5")
    H.advance(be, 2000)
    pkg = H.bu_package(be)
    _man, members = rehash_zip(pkg.path)
    rec = json.loads(members["bringup.json"])
    errs = f_validate(rec, json.loads(RECORD_SCHEMA.read_text(encoding="utf-8")))
    assert not errs, errs[:10]
    doc = shipped_doc()
    assert [s["id"] for s in rec["steps"]] == [s["id"] for s in doc["steps"]]
    by = {s["id"]: s for s in rec["steps"]}
    a_id, ea = put["a"]
    assert by[a_id]["entries"] == ea and by[a_id]["state"] == "PASS"
    assert by[put["b"][0]]["state"] == "FAIL" and by[put["skip"]]["state"] == "SKIPPED"
    assert by["6.5"]["auto"]["state"] == "PASS" and by["6.5"]["auto"]["text"]
    page = members["report.html"].decode("utf-8")
    hp = _Html()
    hp.feed(page)
    hp.close()
    assert not hp.bad, hp.bad
    assert "<script>" not in page and "<img" not in page and "<i>skip" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    text = " ".join(hp.text)
    for s in doc["steps"]:
        assert s["id"] in text, s["id"]
    assert "SKIPPED" in text and "FAIL" in text and "PASS" in text


@pytest.mark.req("SW-BRU-003")
@pytest.mark.defect("SWD-P3-05")
def test_tc_sw_bru_003_02_html_carries_the_csp_of_swr11(bub):
    """TC-SW-BRU-003-02 (CSP as for SWR-11): the bring-up report carries the same script-free Content-Security-Policy
    as the recording reports (SWD-P3-05)."""
    be = bub()
    assert H.bu_start(be).ok
    _m, members = rehash_zip(H.bu_package(be).path)
    page = members["report.html"].decode("utf-8")
    assert re.search(r"http-equiv=.Content-Security-Policy.[^>]*default-src 'none'", page), page[:400]


def _junction(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        pass
    if sys.platform == "win32":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        return r.returncode == 0
    return False


@pytest.mark.req("SW-BRU-003")
def test_tc_sw_bru_003_03_only_app_data_folders(bub, tmp_path):
    """TC-SW-BRU-003-03: planted traps — a calibration sub-folder that is a junction to a folder outside the data
    folder, a foreign recording in the session's recordings root, a session reference (session.json) to a folder
    outside — none of their files is packed; no member name with '..', a drive or a leading '/'."""
    be = bub()
    assert H.bu_start(be).ok
    ready(be, home=False)
    H.bu_select(be, "9.6")
    _home(be)                                                       # a real bring-up recording (allowed root)
    H.advance(be, 1500)
    data = Path(os.environ["BEND_STAND_DATA_DIR"])
    outside = tmp_path / "outside"
    (outside / "secret").mkdir(parents=True)
    (outside / "secret" / "SECRET_JUNCTION.txt").write_text("secret", encoding="utf-8")
    cal = data / "calibration"
    cal.mkdir(parents=True, exist_ok=True)
    have_junction = _junction(cal / "linked", outside / "secret")
    foreign = tmp_path / "rec" / "foreign_recording"
    foreign.mkdir(parents=True)
    (foreign / "SECRET_FOREIGN.txt").write_text("not of this session", encoding="utf-8")
    pkg = H.bu_package(be)
    _m, members = rehash_zip(pkg.path)
    flat = " ".join(members)
    if have_junction:
        assert "SECRET_JUNCTION" not in flat
    assert "SECRET_FOREIGN" not in flat
    assert any(n.startswith("recordings/") for n in members)        # the session's own recording is packed
    for n in members:
        assert ".." not in n.split("/") and not n.startswith("/") and not re.match(r"^[A-Za-z]:", n), n


@pytest.mark.req("SW-BRU-003")
@pytest.mark.defect("SWD-P3-07")
def test_tc_sw_bru_003_03_session_reference_outside_is_not_packed(bub, tmp_path):
    """TC-SW-BRU-003-03 (absolute path in a session reference): a recordings entry planted in ``session.json`` that
    points outside the application's data folders (folder + root) must not bring those files into the package."""
    be = bub()
    assert H.bu_start(be).ok
    folder = Path(H.bu_status(be).folder)
    assert H.bu_close(be).ok
    data = Path(os.environ["BEND_STAND_DATA_DIR"])
    outside = tmp_path / "outside"
    (outside / "secret").mkdir(parents=True)
    (outside / "secret" / "SECRET_REFERENCE.txt").write_text("secret", encoding="utf-8")
    ses_f = folder / "session.json"
    d = json.loads(ses_f.read_text(encoding="utf-8"))
    d["recordings"].append({"step": "9.6", "folder": str(outside / "secret"), "root": str(outside)})
    ses_f.write_text(json.dumps(d), encoding="utf-8")
    out = tmp_path / "pk2"
    assert H.bu_cli_export([str(folder), "--out", str(out), "--data", str(data)]) == 0
    _m, members = rehash_zip(next(out.glob("*.zip")))
    assert not [n for n in members if "SECRET_REFERENCE" in n], sorted(members)


@pytest.mark.req("SW-BRU-003", "SW-BRU-004")
@pytest.mark.parametrize("when", ["no_step", "mid_session", "during_motion", "after_fail"])
def test_tc_sw_bru_003_04_partial_package_at_any_point(bub, tmp_path, when):
    """TC-SW-BRU-003-04: a valid package marked partial with the open steps at every point; packaging changes no step
    state and neither stops nor restarts a running motion step (wire)."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    if when in ("mid_session", "after_fail", "during_motion"):
        s = next(x for x in doc["steps"] if x["kind"] == "confirm")
        H.bu_submit(be, s["id"], fill(s))
    if when == "after_fail":
        s = next(x for x in doc["steps"] if x["kind"] == "measure" and any(f.get("min") is not None
                                                                           for f in x["fields"]))
        e = fill(s)
        nf = next(f for f in s["fields"] if f["kind"] == "number" and f.get("min") is not None)
        e[nf["name"]] = nf["min"] - 1
        H.bu_submit(be, s["id"], e)
    if when == "during_motion":
        ready(be)
        H.bu_select(be, pid)
        assert H.bu_run(be, pid, confirmed=True).ok
        assert H.run_until(be, lambda: "round trip" in H.bu_step(be, pid).action_text, 120_000, 5.0)
    before = {s.id: (s.state, s.action_state) for s in H.bu_steps(be)}
    m = H.wire_mark(be)
    pkg = H.bu_package(be)
    man, members = rehash_zip(pkg.path)
    assert pkg.partial and man["partial"] is True
    rec = json.loads(members["bringup.json"])
    assert rec["partial"] is True and rec["summary"]["not_done"] > 0
    assert sum(1 for s in rec["steps"] if s["state"] == "NOT_DONE") == rec["summary"]["not_done"]
    after = {s.id: (s.state, s.action_state) for s in H.bu_steps(be)}
    assert after == before
    if when == "during_motion":
        assert not H.tx(be, "STOP", since=m) and not H.tx(be, "HALT", since=m)
        assert H.motion(be).moving or H.bu_step(be, pid).action_state == "RUNNING"
        assert action_end(be, pid) == "PASS"
        n_home = len(H.tx(be, "HOME", since=0))
        n_move = len(H.tx(be, "MOVE_ABS", since=0))
        assert n_move == 2 * SHORT["round_trips"] and n_home == 1 + SYS011["baseline_homes"] + 1


@pytest.mark.req("SW-BRU-003")
def test_tc_sw_bru_003_05_offline(bub, monkeypatch, tmp_path):
    """TC-SW-BRU-003-05: the package (assistant and CLI ``bringup-export``) is made with every network call refused."""
    be = bub()
    _complete_small_session(be)
    attempts: list[str] = []

    def guard(*a, **k):  # noqa: ANN002, ANN003
        attempts.append(repr(a)[:80])
        raise OSError("network forbidden by TC-SW-BRU-003-05")
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
    monkeypatch.setattr(socket, "getaddrinfo", guard)
    pkg = H.bu_package(be)
    rehash_zip(pkg.path)
    data = Path(os.environ["BEND_STAND_DATA_DIR"])
    assert H.bu_cli_export(["--out", str(tmp_path / "cli"), "--data", str(data)]) == 0
    rehash_zip(next((tmp_path / "cli").glob("*.zip")))
    assert attempts == []


# ============================================================================================ SW-BRU-004

@pytest.mark.req("SW-BRU-004")
def test_tc_sw_bru_004_01_no_motion_frame_without_the_click(bub, tmp_path):
    """TC-SW-BRU-004-01: viewing every step, evaluating, submitting, marking, closing / resuming the session and
    packing send no motion and no configuration frame; an unconfirmed click and GUI-action steps send nothing."""
    doc = shipped_doc()
    be = bub()
    ready(be)                                                     # motion possible: the assistant still must not
    m0 = H.wire_mark(be)
    assert H.bu_start(be).ok
    for sd in doc["steps"]:
        H.bu_select(be, sd["id"])
        H.advance(be, 120)
        if sd.get("fields"):
            H.bu_submit(be, sd["id"], fill(sd))
        if sd["kind"] in ("info",):
            H.bu_mark(be, sd["id"], "PASS")
        if sd.get("action") and sd["kind"] != "pulse_integrity":
            g = H.bu_run(be, sd["id"], confirmed=True)
            assert g.codes() == ("NOT_AN_ENGINE_ACTION",), (sd["id"], g)
    pid = pulse_id(doc)
    g = H.bu_run(be, pid)
    assert g.needs_confirmation and "BRINGUP_MOTION" in g.codes()
    H.advance(be, 1000)
    sid = H.bu_status(be).session_id
    assert H.bu_close(be).ok
    assert H.bu_resume(be, sid).ok
    H.bu_select(be, pid)
    H.advance(be, 1000)
    H.bu_package(be)
    H.advance(be, 1000)
    sent = {w.name for w in H.tx(be, since=m0)}
    assert not sent & (MOTION_CMDS | CONFIG_CMDS), sorted(sent)
    assert not H.motion(be).moving


def _refusal_states():
    return ["not_enabled", "not_homed", "halt", "paused", "fault", "load_input_invalid", "moving", "owner_conflict"]


@pytest.mark.req("SW-BRU-004", "SYS-011")
@pytest.mark.parametrize("state", _refusal_states())
def test_tc_sw_bru_004_02_refusals_identical_to_the_normal_gates(bub, tmp_path, pdict, state):
    """TC-SW-BRU-004-02: the pulse-integrity click is refused with exactly the normal motion gate of that state
    (``GateResult`` equal to ``motion.check`` of the same move or of HOME), nothing is sent, the step stays not done.
    Owner conflict = the travel-calibration wizard owns the motion (D-54 a)."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    if state != "not_enabled":
        ready(be, home=state != "not_homed")
    tc = None
    if state == "halt":
        H.halt(be)
    elif state == "paused":
        H.pause(be)
    elif state == "fault":
        H.act(be, "wire", input="start", broken=True)
        H.act(be, "wire", input="end", broken=True)
    elif state == "load_input_invalid":
        g = H.set_no_specimen(be, False)
        assert g.ok, g
    elif state == "owner_conflict":
        tc = H.travel_cal(be)
        g = tc.start()
        g = tc.start(confirmed=True) if g.needs_confirmation else g
        assert g.ok, g
        assert H.run_until(be, lambda: tc.state().can_continue and not H.motion(be).moving, 120_000, 5.0)
    if state == "moving":
        H.jog_start(be, +1, 2.0)
        H.advance(be, 200)
        assert H.motion(be).moving
    else:
        H.advance(be, 500)
    v = H.param_default(pdict, "motion.v_max_travel_um_s") / 1000.0
    refs = [H.motion_check(be, "MOVE", speed_mm_s=v, target_mm=float(SHORT["to_mm"])),
            H.motion_check(be, "MOVE", speed_mm_s=v, target_mm=float(SHORT["from_mm"])), H.motion_check(be, "HOME")]
    m = H.wire_mark(be)
    g = H.bu_run(be, pid, confirmed=True)
    if state == "moving":
        H.jog_stop(be)
    if tc is not None:
        tc.cancel()
    assert not g.ok, (state, g)
    assert g == next(r for r in refs if not r.ok), (g, refs)
    want = {"not_enabled": "NOT_ENABLED", "not_homed": "NOT_HOMED", "halt": "HALT", "paused": "PAUSED",
            "fault": "FAULT", "load_input_invalid": "LOAD_INPUT_INVALID", "moving": "MOTION_ACTIVE",
            "owner_conflict": "OWNER_CONFLICT"}[state]
    assert any(want in str(getattr(c, "value", c)) for c in g.codes()), g.codes()
    assert not [w for w in motion_tx(be, m) if w.name != "JOG"]
    assert H.bu_step(be, pid).state == "NOT_DONE" and H.bu_step(be, pid).action_state is None


@pytest.mark.req("SW-BRU-004", "SYS-011")
def test_tc_sw_bru_004_02_confirmations_are_asked_never_preconfirmed(bub, tmp_path):
    """TC-SW-BRU-004-02 (confirmations): the unconfirmed click returns the CONFIRM items of the normal HOME / MOVE
    gates (HOME under unknown load, no-specimen mode) plus BRINGUP_MOTION; nothing moves until confirmed."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    ready(be)
    m = H.wire_mark(be)
    g = H.bu_run(be, pid)
    assert g.needs_confirmation, g
    codes = [str(getattr(c, "value", c)) for c in g.codes()]
    assert "BRINGUP_MOTION" in codes and "HOME_LOAD_CONFIRM" in codes, codes
    for c in H.motion_check(be, "HOME").codes():
        assert str(getattr(c, "value", c)) in codes
    H.advance(be, 2000)
    assert not motion_tx(be, m) and H.bu_step(be, pid).action_state is None


# ============================================================================================ SYS-011

def _run_pulse(be, pid: str, *, inject=None, since_cb=None) -> tuple[str, dict, int]:
    """Confirmed click; ``inject(be)`` once the round trips run; returns (action state, attempt, wire mark)."""
    m = H.wire_mark(be)
    assert H.bu_run(be, pid, confirmed=True).ok
    if inject is not None:
        assert H.run_until(be, lambda: "round trip" in H.bu_step(be, pid).action_text or
                           H.bu_step(be, pid).action_state != "RUNNING", 120_000, 2.0)
        inject(be)
    stt = action_end(be, pid)
    att = session_json(be)["steps"][pid]["action"]["attempts"][-1]
    return stt, att, m


def _check_against_wire(be, att: dict, m: int, *, round_trips: int, to_mm: float, from_mm: float) -> None:
    """The attempt's readings are the HOMED values on the wire; HOMEs only on a homed axis; moves as specified."""
    hv = homed_values(be, m)
    n0 = SYS011["baseline_homes"]
    assert att["d0_um"] == hv[:n0], (att["d0_um"], hv)
    assert all(homes_issued_while_homed(be, m)), "a re-home was issued on an un-homed axis"
    if att.get("d1_um") is not None:
        assert att["d1_um"] == hv[n0], (att["d1_um"], hv)
    mv = H.tx(be, "MOVE_ABS", since=m)
    if att["moves_done"]:
        assert [w.fields["target_um"] for w in mv[: 2 * round_trips]] == \
            [int(to_mm * 1000), int(from_mm * 1000)] * round_trips
    assert not H.tx(be, "SET_PARAM", since=m), "a parameter (speed cap) was written during the check"


@pytest.mark.req("SYS-011", "SW-BRU-004")
def test_tc_sys_011_02_a_clean_run_with_the_shipped_parameters(bub, pdict):
    """TC-SYS-011-02 (a): shipped step parameters = SRS SYS-011; clean run → PASS; 3 baseline re-homes on a homed
    axis, 10 round trips 5 ↔ 285 mm at ``motion.v_max_travel_um_s`` (never above), final re-home; d from the wire."""
    doc = shipped_doc()
    pid = pulse_id(doc)
    p = next(s for s in doc["steps"] if s["id"] == pid)["params"]
    for k, v in SYS011.items():
        assert p[k] == v, (k, p[k], v)
    v_cap = H.param_default(pdict, "motion.v_max_travel_um_s")
    assert p["speed_mm_s"] in ("travel_limit", v_cap / 1000.0), p["speed_mm_s"]      # the full travel speed
    assert "buffer board required" in p["fail_text"]
    be = bub()
    assert H.bu_start(be).ok
    ready(be)
    H.bu_select(be, pid)
    stt, att, m = _run_pulse(be, pid)
    assert stt == "PASS", att
    _check_against_wire(be, att, m, round_trips=10, to_mm=285, from_mm=5)
    mv = H.tx(be, "MOVE_ABS", since=m)
    assert len(mv) == 20 and all(w.fields["v_um_s"] == v_cap for w in mv), {w.fields["v_um_s"] for w in mv}
    assert len(H.tx(be, "HOME", since=m)) == 4
    assert sys011_verdict(att["d0_um"], att["d1_um"], drift_latched_on_wire(be, m)) == "PASS"
    assert att["limit_um"] == max(30, 2 * max(abs(x) for x in att["d0_um"]))
    ses = session_json(be)
    assert ses["steps"][pid]["recordings"], "the pulse-integrity run is not recorded"


@pytest.mark.req("SYS-011")
@pytest.mark.parametrize("shift_um,expect", [(20, "PASS"), (-20, "PASS"), (60, "FAIL"), (-60, "FAIL"),
                                             (300, "FAIL_DRIFT")])
def test_tc_sys_011_02_bc_lost_steps_while_moving(bub, tmp_path, pdict, shift_um, expect):
    """TC-SYS-011-02 (b, c): lost steps injected in the world **during** a full-speed move: above
    ``home.drift_tol_um`` → FW HOME_DRIFT on the re-home → FAIL (one repeat allowed); between 30 µm and the
    tolerance → FAIL without the latch; below 30 µm → PASS with the measured offset (= the world shift)."""
    tol = H.param_default(pdict, "home.drift_tol_um")
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    ready(be)

    def inject(b):
        assert H.run_until(b, lambda: H.motion(b).moving, 20_000, 1.0)
        H.act(b, "world_shift", um=shift_um)
    stt, att, m = _run_pulse(be, pid, inject=inject)
    _check_against_wire(be, att, m, round_trips=SHORT["round_trips"], to_mm=SHORT["to_mm"], from_mm=SHORT["from_mm"])
    d1 = att["d1_um"]
    assert abs(abs(d1) - abs(shift_um)) <= 2, (d1, shift_um)            # the world shift is what the re-home sees
    drift = drift_latched_on_wire(be, m)
    assert drift == (abs(shift_um) > tol)
    assert stt == ("PASS" if expect == "PASS" else "FAIL"), att
    assert sys011_verdict(att["d0_um"], d1, drift) == stt
    s = H.bu_step(be, pid)
    if expect != "PASS":
        assert "one repeat allowed" in s.action_text and "buffer board" not in s.action_text, s.action_text
        assert (("HOME_DRIFT" in s.action_text) == (expect == "FAIL_DRIFT")), s.action_text


@pytest.mark.req("SYS-011")
def test_tc_sys_011_02_b_repeat_then_buffer_board_verdict_in_the_record(bub, tmp_path):
    """TC-SYS-011-02 (b): FAIL, the one repeat (after Clear faults), FAIL again → 'buffer board required' in the step
    and in the bring-up record; a third click → REPEAT_EXHAUSTED, nothing sent."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    ready(be)

    def shift(b):
        assert H.run_until(b, lambda: H.motion(b).moving, 20_000, 1.0)
        H.act(b, "world_shift", um=300)
    stt, _att, _m = _run_pulse(be, pid, inject=shift)
    assert stt == "FAIL"
    H.fault_clear(be)
    H.advance(be, 500)
    ready(be)
    stt, _att, _m = _run_pulse(be, pid, inject=shift)
    assert stt == "FAIL"
    s = H.bu_step(be, pid)
    assert "buffer board required" in s.action_text and s.state == "FAIL"
    H.fault_clear(be)
    H.advance(be, 500)
    m = H.wire_mark(be)
    g = H.bu_run(be, pid, confirmed=True)
    assert g.codes() == ("REPEAT_EXHAUSTED",) and "buffer board required" in g.text()
    H.advance(be, 1000)
    assert not motion_tx(be, m)
    _man, members = rehash_zip(H.bu_package(be).path)
    rec = json.loads(members["bringup.json"])
    st = next(x for x in rec["steps"] if x["id"] == pid)
    assert st["state"] == "FAIL" and "buffer board required" in st["action"]["text"]
    assert [a["verdict"] for a in st["action"]["attempts"]] == ["DRIFT", "DRIFT"]
    assert "buffer board required" in members["report.html"].decode("utf-8")


@pytest.mark.req("SYS-011")
@pytest.mark.parametrize("d0_shift,run_shift,expect", [(40, 60, "PASS"), (40, 95, "FAIL")])
def test_tc_sys_011_02_limit_follows_the_baseline_scatter(bub, tmp_path, d0_shift, run_shift, expect):
    """TC-SYS-011-02: |d1| ≤ max(30 µm, 2·max|d0ᵢ|) — a baseline scatter of 40 µm (world shift between baseline
    re-homes 1 and 2) raises the limit to 80 µm: 60 µm passes, 95 µm fails."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    ready(be)
    H.act(be, "on_event", code="HOMED", nth=1, then={"action": "world_shift", "um": d0_shift})

    def inject(b):
        assert H.run_until(b, lambda: H.motion(b).moving, 20_000, 1.0)
        H.act(b, "world_shift", um=run_shift)
    stt, att, m = _run_pulse(be, pid, inject=inject)
    assert max(abs(x) for x in att["d0_um"]) == pytest.approx(d0_shift, abs=2), att["d0_um"]
    assert att["limit_um"] == pytest.approx(2 * max(abs(x) for x in att["d0_um"]))
    assert stt == expect == sys011_verdict(att["d0_um"], att["d1_um"], drift_latched_on_wire(be, m)), att


@pytest.mark.req("SYS-011")
def test_tc_sys_011_02_baseline_scatter_stops_without_moves(bub, tmp_path):
    """TC-SYS-011-02: max|d0ᵢ| > 100 µm → FAIL 'START switch mounting' (not a buffer verdict, not a counted repeat),
    no round trip is sent."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    ready(be)
    H.act(be, "world_shift", um=150)
    stt, att, m = _run_pulse(be, pid)
    assert stt == "FAIL" and att["verdict"] == "BASELINE_SCATTER", att
    assert not H.tx(be, "MOVE_ABS", since=m)
    assert sys011_verdict(homed_values(be, m), None, False) == "BASELINE_SCATTER"
    s = H.bu_step(be, pid)
    assert "START switch" in s.action_text and "buffer board required" not in s.action_text
    assert session_json(be)["steps"][pid]["action"]["verdict_fails"] == 0


def _dist_estop(be):
    H.act(be, "estop", open=True)


def _dist_alm(be):
    H.act(be, "alm", active=True)


def _dist_link(be):
    H.act(be, "inject", fault="hang", duration_ms=2500)


def _dist_limit(be):
    H.act(be, "limit", name="end", active=True)


def _dist_stop(be):
    H.stop(be)


def _dist_halt(be):
    H.halt(be)


def _dist_cancel(be):
    H.bu_cancel(be)


DISTURB = {"estop": _dist_estop, "alm": _dist_alm, "link_loss": _dist_link, 
           "limit_stop": _dist_limit, "operator_stop": _dist_stop, "hotkey_halt": _dist_halt, "cancel": _dist_cancel}


@pytest.mark.req("SYS-011", "SW-BRU-004")
@pytest.mark.parametrize("what", sorted(DISTURB))
def test_tc_sys_011_02_disturbance_aborts_and_never_restarts(bub, tmp_path, what):
    """TC-SYS-011-02 / TC-SW-BRU-004-03 (API part): E-stop, ALM, link loss, driver power, a limit stop, the operator's
    STOP, the Pause/Break HALT or Cancel during the round trips → ABORTED, readings discarded, not a counted repeat,
    nothing sent by the assistant afterwards (no automatic retry)."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    ready(be)

    def inject(b):
        assert H.run_until(b, lambda: H.motion(b).moving, 20_000, 1.0)
        DISTURB[what](b)
    stt, att, m = _run_pulse(be, pid, inject=inject)
    s = H.bu_step(be, pid)
    if what == "limit_stop":                       # a limit reached during a stroke move: stop + report (FAIL)
        assert stt in ("ABORTED", "FAIL") and "limit" in s.action_text.lower(), (stt, s.action_text)
    else:
        assert stt == "ABORTED", att
        assert s.state == "NOT_DONE" and ("again" in s.action_text or "cancel" in s.action_text), s
    assert "buffer board required" not in s.action_text
    assert session_json(be)["steps"][pid]["action"]["verdict_fails"] == 0
    if what == "cancel":
        assert H.tx(be, "STOP", since=m), "Cancel did not use the normal STOP"
    mark = H.wire_mark(be)
    H.advance(be, 5000)
    assert not motion_tx(be, mark), [w.name for w in motion_tx(be, mark)]


@pytest.mark.req("SYS-011", "SW-BRU-004")
def test_tc_sys_011_02_speed_cap_refusal_is_a_fail_and_no_cap_is_raised(bub, tmp_path, pdict):
    """TC-SYS-011-02 (E_RANGE / cap rule): when the travel speed exceeds a cap (here: the step-rate cap after a
    larger steps/mm) the check is FAIL with the raw refusal recorded; no parameter is written, nothing moves."""
    doc, pid = pulse_doc(**SHORT)
    be = bub(steps=write_doc(tmp_path, doc))
    assert H.bu_start(be).ok
    ready(be)
    H.result(be, H.write_verify(be, {"motion.steps_per_mm": 3000.0}))
    H.advance(be, 500)
    m = H.wire_mark(be)
    g0 = H.bu_run(be, pid)
    g = H.bu_run(be, pid, confirmed=True)
    H.advance(be, 1000)
    s = H.bu_step(be, pid)
    assert not g.ok and not g.needs_confirmation and not g0.ok, (g0, g)          # refused, also unconfirmed
    assert re.search(r"speed|cap|limit", g.text(), re.I), g.text()                # the reason is shown
    assert s.state != "PASS" and s.action_state != "RUNNING", s
    assert not motion_tx(be, m) and not H.tx(be, "SET_PARAM", since=m)            # nothing moves, no cap raised
    assert H.config_values(be)["motion.v_max_travel_um_s"] == H.param_default(pdict, "motion.v_max_travel_um_s")


def _i_led_steps(doc: dict) -> tuple[list[tuple[dict, dict]], list[tuple[dict, dict]]]:
    """(V fields over the 100 Ω shunt, mA fields of the low-burden repeat) as (step, field) pairs, found by unit and
    limit (SYS-011: 6 mA = 0.60 V over 100 Ω; a 4.5–6 mA shunt reading needs the low-burden repeat)."""
    v = [(s, f) for s in doc["steps"] if s["kind"] == "measure" for f in s.get("fields", ())
         if f["kind"] == "number" and f.get("unit") == "V" and f.get("min") is not None and 0.44 <= f["min"] <= 0.61
         and "I_LED" in s.get("expected", "")]
    ma = [(s, f) for s in doc["steps"] if s["kind"] == "measure" for f in s.get("fields", ())
          if f["kind"] == "number" and f.get("unit") == "mA" and f.get("min") == pytest.approx(6)]
    return v, ma


def _entries_result(be, sid: str) -> str | None:
    return session_json(be)["steps"][sid]["entries_result"]


@pytest.mark.req("SYS-011", "SW-BRU-001")
def test_tc_sys_011_04_i_led_entries(bub):
    """TC-SYS-011-04 (SRS SYS-011 a): over the 100 Ω shunt < 0.45 V (4.5 mA) → FAIL, ≥ 0.60 V → PASS, 0.45…0.60 V →
    the low-burden repeat is required (it cannot be declared unnecessary) and decides: < 6 mA → FAIL, ≥ 6 mA → PASS;
    a value typed with its unit is refused; the values appear in the package."""
    doc = shipped_doc()
    v_fields, ma_fields = _i_led_steps(doc)
    assert len({s["id"] for s, _f in v_fields}) >= 2, "ENA and DIR I_LED steps (V over the 100 Ω shunt) expected"
    be = bub()
    assert H.bu_start(be).ok
    for s, f in v_fields:
        base = fill(s)
        for v, want in ((0.40, "FAIL"), (0.60, "PASS"), (1.2, "PASS")):
            iss = H.bu_submit(be, s["id"], {**base, f["name"]: v})
            assert _entries_result(be, s["id"]) == want, (s["id"], f["name"], v, iss)
            assert (H.bu_step(be, s["id"]).state == "FAIL") == (want == "FAIL")
        iss = H.bu_submit(be, s["id"], {**base, f["name"]: "0.4 V"})
        assert iss and H.bu_step(be, s["id"]).entries[f["name"]] == 1.2          # refused, nothing stored
    last: dict[str, Any] = {}
    if ma_fields:
        rs = ma_fields[0][0]
        for s, f in v_fields:                                                   # a band reading on every input
            H.bu_submit(be, s["id"], {**fill(s), f["name"]: 0.50})
            assert H.bu_step(be, s["id"]).state != "FAIL", (s["id"], "0.50 V is the repeat band, not a fail")
        choice = next((x for x in rs["fields"] if x["kind"] == "choice"), None)
        base = {x["name"]: v for x, v in ((x, fill({"fields": [x]})[x["name"]]) for x in rs["fields"])
                if x["kind"] != "number"}
        if choice is not None:
            iss = H.bu_submit(be, rs["id"], {**base, choice["name"]: choice["choices"][0]})
            assert any(str(i.severity.value).upper() == "ERROR" for i in iss),                 ("'no repeat needed' accepted with a band reading", iss)
            base[choice["name"]] = choice["choices"][-1]
        nums = {f["name"]: 12.0 for s, f in ma_fields if s["id"] == rs["id"]}
        for f_name in nums:
            for v, want in ((5.9, "FAIL"), (6.0, "PASS"), (12.0, "PASS")):
                iss = H.bu_submit(be, rs["id"], {**base, **nums, f_name: v})
                assert _entries_result(be, rs["id"]) == want, (rs["id"], f_name, v, iss)
        H.bu_submit(be, rs["id"], {**base, **nums, next(iter(nums)): 5.9})
        last[rs["id"]] = 5.9
    s0, f0 = v_fields[0]
    H.bu_submit(be, s0["id"], {**fill(s0), f0["name"]: 0.40})
    last[s0["id"]] = 0.40
    _m, members = rehash_zip(H.bu_package(be).path)
    rec = {x["id"]: x for x in json.loads(members["bringup.json"])["steps"]}
    for sid, v in last.items():
        assert v in rec[sid]["entries"].values() and rec[sid]["state"] == "FAIL", rec[sid]


@pytest.mark.req("SYS-011")
@pytest.mark.defect("SWD-P3-06")
def test_tc_sys_011_04_i_led_fail_states_buffer_board_required(bub):
    """TC-SYS-011-04: an I_LED reading below 4.5 mA over the shunt ends with 'buffer board required' in the record and
    the HTML (SYS-011: (a) or (b) failing → buffer board), as the pulse-integrity verdict does (SWD-P3-06)."""
    v_fields, _ma = _i_led_steps(shipped_doc())
    s, f = v_fields[0]
    be = bub()
    assert H.bu_start(be).ok
    H.bu_submit(be, s["id"], {**fill(s), f["name"]: 0.40})
    _m, members = rehash_zip(H.bu_package(be).path)
    rec = next(x for x in json.loads(members["bringup.json"])["steps"] if x["id"] == s["id"])
    assert rec["state"] == "FAIL"
    assert "buffer board" in json.dumps(rec, ensure_ascii=False).lower(), rec
    page = members["report.html"].decode("utf-8")
    row = page[page.index(s["id"]):][:3000]
    assert "buffer board" in row.lower()


# ============================================================================================ SYS-011 on the twin

def _twin_exe():
    import build as twin_build  # noqa: PLC0415

    try:
        twin_build.find_gcc()
    except SystemExit as e:  # pragma: no cover - environment
        pytest.skip(f"no host gcc for the twin: {e}")
    if not twin_build.fw_core_present():  # pragma: no cover
        pytest.skip("A's FW core absent")
    return twin_build.ensure_built("fw")


def _rt_wait(be, pred, timeout_s: float) -> bool:
    """Real clock: wait with the GUI thread's beat (liveness supervision stops motion without it, SWR-01)."""
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        H.gui_beat(be)
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


@pytest.mark.twin
@pytest.mark.rt
@pytest.mark.req("SYS-011", "SW-BRU-004")
def test_tc_sys_011_03_pulse_integrity_on_the_twin(tmp_path, pdict):
    """TC-SYS-011-03: the same step against A's firmware (host twin, real clock, private build dir): a clean run
    passes; 60 µm lost while moving → FAIL without the latch; 300 µm lost while moving → the FW latches HOME_DRIFT
    (wire) → FAIL with the buffer-board verdict (second fail = the repeat). d1 = the HOMED value of the wire."""
    from twin import Twin  # noqa: PLC0415

    doc, pid = pulse_doc(**SHORT)
    steps = write_doc(tmp_path, doc)
    tw = Twin("realtime", exe=_twin_exe(), run_dir=tmp_path / "twin", speed=1.0)
    proc = tw.eng.p
    H.log_process("fw_twin engine", proc.pid, "started")
    port, _ctl = tw.serve(0, 0)
    be = H.realtime_backend(recordings_root=str(tmp_path / "rec"), bringup_steps=steps)
    try:
        H.connect(be, f"tcp://127.0.0.1:{port}").result(15)
        assert _rt_wait(be, lambda: H.status(be).stream.on and H.status(be).board is not None, 10)
        assert H.bu_start(be).ok
        H.no_specimen(be)
        assert H.enable(be).ok
        assert _rt_wait(be, lambda: H.motion(be).enabled, 5)
        H.write_verify(be, {"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}).result(10)
        fut = H.home(be, load_confirmed=True)
        assert _rt_wait(be, fut.done, 90) and _rt_wait(be, lambda: H.motion(be).homed and not H.motion(be).moving, 5)
        H.bu_select(be, pid)

        def run(shift_um: int | None) -> tuple[str, dict, int]:
            m = H.wire_mark(be)
            g = H.bu_run(be, pid, confirmed=True)
            assert g.ok, g
            if shift_um is not None:
                assert _rt_wait(be, lambda: "round trip" in H.bu_step(be, pid).action_text, 120)
                assert _rt_wait(be, lambda: H.motion(be).moving, 10)
                assert tw.act("world_shift", um=float(shift_um))["ok"]           # lost steps while moving
            assert _rt_wait(be, lambda: H.bu_step(be, pid).action_state in ("PASS", "FAIL", "ABORTED"), 240), \
                H.bu_step(be, pid)
            time.sleep(0.5)
            ses = json.loads((Path(H.bu_status(be).folder) / "session.json").read_text(encoding="utf-8"))
            return H.bu_step(be, pid).action_state, ses["steps"][pid]["action"]["attempts"][-1], m

        stt, att, m = run(None)
        assert stt == "PASS", att
        hv = [int(w.fields["value"]) for w in H.rx(be, "EVENT", since=m) if w.fields.get("code") == "HOMED"]
        assert att["d0_um"] == hv[:3] and att["d1_um"] == hv[3], (att, hv)
        assert [w.fields["target_um"] for w in H.tx(be, "MOVE_ABS", since=m)] == [45000, 5000] * 2
        v_cap = H.param_default(pdict, "motion.v_max_travel_um_s")
        assert {w.fields["v_um_s"] for w in H.tx(be, "MOVE_ABS", since=m)} == {v_cap}
        stt, att, m = run(60)
        assert stt == "FAIL" and abs(abs(att["d1_um"]) - 60) <= 3 and not att["home_drift"], att
        assert not any(w.fields.get("code") == "FAULT_SET" for w in H.rx(be, "EVENT", since=m))
        stt, att, m = run(300)
        assert stt == "FAIL" and att["home_drift"], att
        ev = [w.fields for w in H.rx(be, "EVENT", since=m) if w.fields.get("code") == "FAULT_SET"]
        assert ev, "no FAULT_SET (HOME_DRIFT) from the FW"
        s = H.bu_step(be, pid)
        assert "buffer board required" in s.action_text, s.action_text
    finally:
        be.shutdown()
        tw.close()
        assert tw.eng is None and proc.poll() is not None
        H.log_process("fw_twin engine", proc.pid, f"stopped rc={proc.returncode}")


# ============================================================================================ found on the way

@pytest.mark.req("SW-BRU-002")
@pytest.mark.defect("SWD-P3-09")
def test_board_reset_with_default_parameters_is_not_an_event_loss(bub):
    """The bring-up guide resets the board several times before the first Save to NVM (8.1, 8.7, 12.3): the EVENT SEQ
    restart (0 = PARAMS_DEFAULTED, 1 = BOOT, ICD §6.4 'parameters per §11.3; EVENT BOOT queued') is not a loss — no
    FW_EVENTS_LOST incident may reach the incident log / the bring-up package."""
    be = bub()
    lost: list[dict] = []
    H.subscribe(be, "log", lambda r: lost.append(r.payload) if r.payload.get("kind") == "FW_EVENTS_LOST" else None)
    H.act(be, "estop", open=True)
    H.advance(be, 300)
    m = H.wire_mark(be)
    H.act(be, "reset", cause="pin")
    H.advance(be, 3000)
    seqs = [(w.seq, w.fields["code"]) for w in H.events(be, since=m)]
    assert seqs[:2] == [(0, "PARAMS_DEFAULTED"), (1, "BOOT")], seqs              # nothing was lost on the wire
    assert not lost, lost


# ============================================================================================ data safety

_STRAY = r'''
import sys
import harness as H

be = H.lockstep_backend()                                   # no data dir, no recordings root, no package folder
assert H.bu_start(be, confirmed=True).ok
assert H.record_start(be).ok
H.advance(be, 500)
H.record_stop(be)
H.advance(be, 500)
pkg = H.bu_package(be)
H.result(be, H.save_nvm(be))
be.shutdown()
rt = H.realtime_backend()
H.connect(rt, "sim").result(10)
rt.shutdown()
print("STRAY-OK", pkg.path)
'''


@pytest.mark.req("SW-BRU-002", "SW-BRU-003")
def test_harness_outside_pytest_never_touches_the_user_folders(tmp_path):
    """Data-safety rule (incident 2026-10-14): the validation harness run as a plain script — outside the conftest
    isolation, no data dir / recordings root / package folder given, no BEND_STAND_* env — creates nothing under
    ``%APPDATA%`` or the user profile (Documents); everything goes to a temp folder (``core.testing.isolated_settings``)."""
    appdata, home, tmpd = tmp_path / "fake_appdata", tmp_path / "fake_home", tmp_path / "tmp"
    for d in (appdata, home, tmpd):
        d.mkdir()
    (home / "Documents").mkdir()
    script = tmp_path / "stray.py"
    script.write_text(_STRAY, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BEND_STAND_") and not k.startswith("PYTEST")}
    env.update(APPDATA=str(appdata), LOCALAPPDATA=str(appdata / "Local"), USERPROFILE=str(home), HOME=str(home),
               HOMEDRIVE="", HOMEPATH="", TEMP=str(tmpd), TMP=str(tmpd), BEND_STAND_HOTKEY="off",
               PYTHONPATH=os.pathsep.join([str(SW / "src"), str(Path(__file__).parent), str(REPO / "00_System" / "tools")]))
    p = subprocess.Popen([sys.executable, str(script)], env=env, cwd=str(tmp_path), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    H.log_process("stray harness script", p.pid, "started")
    try:
        out, _ = p.communicate(timeout=120)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(10)
        H.log_process("stray harness script", p.pid, f"ended rc={p.returncode}")
    text = out.decode(errors="replace")
    assert p.returncode == 0 and "STRAY-OK" in text, text[-3000:]
    made = [str(x.relative_to(tmp_path)) for root in (appdata, home) for x in root.rglob("*")]
    assert made == [str(Path("fake_home") / "Documents")], made          # only the folder made by this test
    assert any(tmpd.rglob("*.zip")), "the package should be in the temp folder"

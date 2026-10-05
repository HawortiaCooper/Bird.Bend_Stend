"""M4 (sequencer) integration support: sequence documents, Implementer B's sequencer driven in lock-step virtual
time, and **wire-level oracles** that do not depend on B's result layouts (Integrator, M4 / D-46).

The sequence under test is written as a ``bird.bend.sequence`` v1 file (SW_design §13.4, the frozen file contract)
and loaded through ``Backend.sequencer.load`` — so these tests depend on B's public API (§15.1) and the file
schema only, never on B's internal classes. Outcomes are judged from the FW side of the wire (the twin's
``wire_log``: DATA frames with their VALID / MOVING bits, EVENTs with device time, the PC's requests and the FW's
responses) and, where the SRS names B-side results (step status NOT_REACHED, end reason BREAK_DETECTED, the report
numbers), through B's status / report objects searched by keyword (layouts not frozen before delivery).

While B's ``core.sequencer`` is absent (or still the M4 stub) every test using it is **xfail** with that reason
(``needs_b`` marker at collection, ``NotImplementedError`` / NOT_IMPLEMENTED at run time); the tests flip by
themselves when B's code lands. A failure then is a finding (Integrator test or B's code), never a silent change
of an expectation.

Implements: SYS-008 (SW<->twin integration support), M4 integration (D-46)
"""
from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

import ref_codec as rc

FRAME_US = 12_500                         # 80 SPS DATA period (twin rate_error 0)
SLACK_US = 3_000                          # wire (26 B ≈ 0.28 ms) + one 1 ms lock-step backend step + FW main loop
ACTIVE = {"PREPARING", "RUNNING", "PAUSED", "WAITING_OPERATOR", "STOPPING", "STARTING"}
TERMINAL = {"FINISHED", "STOPPED", "ABORTED", "ERROR", "ENDED", "FAILED", "DONE"}
MOTION_CMDS = ("MOVE_ABS", "MOVE_UNTIL_LOAD", "JOG", "HOME")
# ICD §7.6 steady-state window rule (SW-REP-002, D-33 b): excluded DATA flags bits 4–7 and status bits 0–8, 13, 14
SS_EXCL_FLAGS = {"ESTOP", "HALT", "FAULT", "OVERRUN"}
SS_EXCL_STATUS = {"PAUSED", "LIMIT_START", "LIMIT_END", "LOAD_LIMIT", "AFE_STALE", "AFE_SATURATED", "AFE_SETTLING",
                  "AFE_RATE_MISMATCH", "LINK_WDG", "POS_UNCERTAIN", "NO_AFE_DATA"}


# ============================================================================================ sequence documents
def step(uid: str, kind: str, target: float | None = None, **kw: Any) -> dict:
    """One ``steps[]`` entry of a ``bird.bend.sequence`` v1 file (SW_design §10.1 field names)."""
    d: dict[str, Any] = {"uid": uid, "kind": kind}
    if target is not None:
        d["target"] = float(target)
    d.update(kw)
    return d


def seq_doc(name: str, steps: list[dict], loops: Iterable[dict] = (), *, k_est: float = 50.0, pull_dir: int = 1,
            travel_ref: str = "test", defaults: dict | None = None) -> dict:
    """Schema version 1 (SW_design §13.4, the published contract; B's reader migrates it — SRS v0.6.2 removed
    ``on_trim_fail``, so it is not written: a trim that does not converge is NOT_REACHED)."""
    return {"schema": "bird.bend.sequence", "schema_version": 1, "name": name, "travel_ref": travel_ref,
            "k_est_n_mm": float(k_est), "pull_dir": int(pull_dir),
            "defaults": defaults or {"speed_mm_s": 5.0, "accel_mm_s2": 0.0, "settle_s": 0.5, "capture_s": 1.0,
                                     "tol_n": 2.0},
            "steps": steps, "loops": list(loops), "notes": "Integrator M4 integration test"}


# ============================================================================================ B's sequencer
def _stub(obj: Any) -> str | None:
    items = getattr(obj, "items", ()) or ()
    for i in items:
        if "NOT_IMPLEMENTED" in str(getattr(i, "code", "")):
            return getattr(i, "text", "NOT_IMPLEMENTED")
    return None


def load_sequence(be, doc: dict, folder: Path):  # noqa: ANN001, ANN201
    """Write ``doc`` as ``<name>.bbseq.json`` and load it through ``Backend.sequencer.load`` (§10.7)."""
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{doc['name'].replace(' ', '_')}.bbseq.json"
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    try:
        r = be.sequencer.load(str(p))
    except NotImplementedError as e:
        pytest.xfail(f"Implementer B's sequencer.load not delivered yet ({e})")
    if isinstance(r, tuple):                       # (Sequence, warnings) — B's seqfile.load shape
        r, warns = r
        assert not warns, warns
    issues = be.sequencer.validate(r)
    assert not [i for i in issues if "ERROR" in str(getattr(i, "severity", ""))], issues
    return r


def sequencer_stub(be) -> str | None:  # noqa: ANN001
    """Reason while B's sequencer facade is still the M4 stub (checked before an expensive test setup)."""
    try:
        be.sequencer.new()
    except NotImplementedError as e:
        return f"Implementer B's sequencer facade not delivered yet ({e})"
    g = be.sequencer.start(be.sequencer.new())     # not connected: a real gate refuses, the stub says NOT_IMPLEMENTED
    if s := _stub(g):
        return f"Implementer B's sequencer.start not delivered yet ({s})"
    return None


def start_sequence(be, seq, *, expect_ok: bool = True):  # noqa: ANN001, ANN201
    g = be.sequencer.start(seq, confirmed=True)
    if s := _stub(g):
        pytest.xfail(f"Implementer B's sequencer.start not delivered yet ({s})")
    if expect_ok:
        assert g.ok, f"sequence start refused: {flatten(g)}"
    return g


def state_name(st: Any) -> str:
    s = getattr(st, "state", "")
    return str(getattr(s, "name", s)).upper()


def outcome_text(st: Any) -> str:
    """State + end reason (SW_design §15.5f B6-10, verbatim codes) + message of a SeqStatus."""
    return " ".join(str(x) for x in (state_name(st), getattr(st, "end_reason", ""), getattr(st, "message", ""))).upper()


def run_sequence(rig, timeout_ms: float, *, step_ms: float = 2.0,  # noqa: ANN001
                 on_tick: Callable[[Any], bool | None] | None = None) -> Any:
    """Advance virtual time until the sequence reaches a terminal state. ``on_tick(status)`` runs every step
    (world actions, controls); returning True ends the wait early (status returned, sequence maybe active)."""
    be = rig.be
    started = False
    t_end = rig.now_ms + timeout_ms
    while rig.now_ms < t_end:
        st = be.sequencer.status()
        if state_name(st) in ACTIVE:
            started = True
        if on_tick is not None and on_tick(st):
            return st
        if state_name(st) in TERMINAL or (started and state_name(st) == "IDLE"):
            rig.advance(50)
            return be.sequencer.status()
        rig.advance(step_ms, 1.0)
    raise AssertionError(f"sequence not ended after {timeout_ms} ms: {flatten(be.sequencer.status())}")


def flatten(obj: Any) -> str:
    try:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            obj = dataclasses.asdict(obj)
        return json.dumps(obj, default=str)
    except (TypeError, ValueError):
        return str(obj)


# ============================================================================================ wire oracles
def _payload(hexs: str) -> tuple[int, int, bytes]:
    b = bytes.fromhex(hexs)
    n = int.from_bytes(b[4:6], "little")
    return b[2], b[3], b[6:6 + n]


def rx_cmds(tw, since_us: float = 0.0, names: Iterable[str] | None = None) -> list[dict]:  # noqa: ANN001
    """PC → FW requests that reached the FW (twin wire_log), decoded: name, seq, fields, wire times (world µs)."""
    want = set(names) if names else None
    out = []
    for w in tw.wire_log:
        if w["dir"] != "rx" or w["first_us"] < since_us:
            continue
        ftype, seq, pl = _payload(w["hex"])
        name = rc.CMD_NAME.get(ftype)
        if name is None or (want and name not in want):
            continue
        try:
            fields = rc.decode_request(name, pl)
        except (ValueError, KeyError):
            fields = {"raw": pl.hex()}
        out.append({"name": name, "seq": seq, "fields": fields, "first_us": w["first_us"], "last_us": w["last_us"]})
    return out


def tx_responses(tw) -> dict[tuple[str, int], dict]:  # noqa: ANN001
    """FW responses by (command name, seq), decoded, with the wire time of the last byte."""
    out = {}
    for w in tw.wire_log:
        if w["dir"] != "tx":
            continue
        ftype, seq, pl = _payload(w["hex"])
        if ftype & rc.RESP_BIT and ftype not in rc.ASYNC_NAME:
            name = rc.CMD_NAME.get(ftype & ~rc.RESP_BIT)
            if name:
                d = rc.decode_response(name, pl)
                d["last_us"] = w["last_us"]
                out[(name, seq)] = d
    return out


def events(tw, since_us: float = 0.0, codes: Iterable[str] | None = None) -> list[dict]:  # noqa: ANN001
    want = set(codes) if codes else None
    out = []
    for w in tw.wire_log:
        if w["dir"] == "tx" and w["type"] == rc.ASYNC["EVENT"] and w["first_us"] >= since_us:
            e = rc.decode_event(_payload(w["hex"])[2])
            if want is None or e["code"] in want:
                e["wire_us"] = w["last_us"]
                out.append(e)
    return out


def data(tw, since_us: float = 0.0) -> list[dict]:  # noqa: ANN001
    out = []
    for w in tw.wire_log:
        if w["dir"] == "tx" and w["type"] == rc.ASYNC["DATA"] and w["first_us"] >= since_us:
            d = rc.decode_data(_payload(w["hex"])[2])
            d["wire_us"] = w["last_us"]
            out.append(d)
    return out


def valid_runs(frames: list[dict]) -> list[list[dict]]:
    """Maximal runs of consecutive DATA frames with VALID = 1 (one per VALID window)."""
    runs: list[list[dict]] = []
    cur: list[dict] = []
    for d in frames:
        if "VALID" in d["flags"]:
            cur.append(d)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def set_valid_windows(tw, since_us: float = 0.0) -> list[dict]:  # noqa: ANN001
    """SET_VALID requests with the FW's applied device time ``t_us`` (response), in wire order."""
    resp = tx_responses(tw)
    out = []
    for c in rx_cmds(tw, since_us, ("SET_VALID",)):
        r = resp.get(("SET_VALID", c["seq"]), {})
        out.append({"valid": c["fields"]["valid"], "t_us": r.get("t_us"), "status": r.get("status"),
                    "wire_us": c["first_us"]})
    return out


def steady_frames(frames: list[dict], t0_us: float, t1_us: float) -> list[dict]:
    """ICD §7.6 / SW-REP-002 window rule over device time [t0, t1]: VALID, not MOVING, none of the excluded bits,
    setpoint equal to its value at the window start."""
    sel = [d for d in frames if t0_us <= d["t_us"] <= t1_us and "VALID" in d["flags"] and "MOVING" not in d["flags"]
           and not SS_EXCL_FLAGS & set(d["flags"]) and not SS_EXCL_STATUS & set(d["status"])]
    if not sel:
        return sel
    sp0 = sel[0]["setpoint_um"]
    return [d for d in sel if d["setpoint_um"] == sp0]


def mean(xs: list[float]) -> float:
    return math.fsum(xs) / len(xs)


def no_motion_after(tw, t_us: float) -> list[dict]:  # noqa: ANN001
    """Motion commands or SET_VALID(1) that reached the FW after world time ``t_us`` (must be empty after an
    abort / terminal stop: the sequence sends nothing afterwards)."""
    return [c for c in rx_cmds(tw, t_us) if c["name"] in MOTION_CMDS
            or (c["name"] == "SET_VALID" and c["fields"].get("valid") == 1)]


# ============================================================================================ report helpers
def recording_folder(root: Path) -> Path:
    folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    assert folders, f"no recording folder under {root}"
    return folders[-1]


def find_words(obj: Any, *words: str) -> bool:
    text = flatten(obj).upper()
    return all(w.upper() in text for w in words)

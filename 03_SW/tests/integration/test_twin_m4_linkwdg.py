"""Link watchdog replay of ``vectors/linkwdg_vectors.json`` against A's firmware in the twin (ICD v0.7.4 §9.1,
§6.2; SAF-FW-015, SAF-FW-001; D-15, D-47 a).

Per case: boot, stream on, ``safety.link_timeout_ms`` set, the motion state set up (NOT_ENABLED; IDLE = ENABLE;
MOVE_ABS = ENABLE + HOME + a long move; MOVE_UNTIL_LOAD = the same with an unreachable threshold), then SET_VALID
``valid_before`` as the **last** command: the silence is measured from the end of that frame (twin wire log). The
PC stays silent for ``silence_ms``:
- below the timeout: no frame produced since, VALID unchanged in the following DATA frames;
- at timeout + 2 ms: the reaction has started (the first EVENT produced ≤ timeout + 2 ms after the last frame);
  200 ms later (still silent): the EVENT set equals ``expect.events``, DATA VALID = ``valid_after`` and the status
  bit LINK_WDG = ``link_wdg_status``; then one PING (a valid frame): the EVENT set since equals
  ``after_frame_events`` and LINK_WDG = ``link_wdg_after_frame``.

The not-moving cases with VALID = 1 that trip are new in ICD v0.7.4 (D-47 a); A's FW implements them (they passed
on the first run against A's tree, so no xfail).

Verifies: SAF-FW-015, SAF-FW-001, D-47
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import gen_params
import ref_codec as rc
from twin import TwinLink

pytestmark = pytest.mark.twin

VEC = json.loads((Path(gen_params.__file__).resolve().parent / "vectors" / "linkwdg_vectors.json")
                 .read_text(encoding="utf-8"))
PD = gen_params.load()
PID = {p.key: p.id for p in PD.params}
PTYPE = {p.key: p.type for p in PD.params}


def _cmd(tw, link: TwinLink, name: str, fields: dict | None = None) -> dict:  # noqa: ANN001
    r = link.cmd(name, fields or {})
    assert r["status"] == "OK", (name, fields, r)
    return r


def _run(tw, link: TwinLink, ms: float) -> None:  # noqa: ANN001
    end = tw.now_us + ms * 1000
    while tw.now_us < end:
        link.send("PING")
        tw.advance_us(min(150_000.0, end - tw.now_us))
    link.poll()


def _setup(tw, link: TwinLink, case: dict) -> None:  # noqa: ANN001
    tw.advance_ms(5)
    _cmd(tw, link, "STREAM_START")
    _cmd(tw, link, "SET_PARAM", {"id": PID["safety.link_timeout_ms"], "type": PTYPE["safety.link_timeout_ms"],
                                 "value": case["link_timeout_ms"]})
    st = case["motion_state"]
    if st == "NOT_ENABLED":
        return
    r = _cmd(tw, link, "ENABLE")
    _run(tw, link, r.get("settle_ms", 500) + 20)
    if st == "IDLE":
        return
    _cmd(tw, link, "HOME", {"flags": 0})
    for _ in range(300):
        _run(tw, link, 100)
        if [e for e in link.events() if e["code"] == "HOMED"]:
            break
    else:
        raise AssertionError("not homed")
    _run(tw, link, 50)
    if st == "MOVE_ABS":
        _cmd(tw, link, "MOVE_ABS", {"target_um": 200_000, "v_um_s": 10_000, "a_um_s2": 0})
    else:
        _cmd(tw, link, "MOVE_UNTIL_LOAD", {"bound_um": 200_000, "v_um_s": 10_000, "a_um_s2": 0,
                                           "raw_stop": 7_000_000, "cmp": 0})
    _run(tw, link, 300)


def _events_since(tw, t_us: float) -> list[list]:  # noqa: ANN001
    out = []
    for w in tw.wire_log:
        if w["dir"] == "tx" and w["type"] == rc.ASYNC["EVENT"] and w["first_us"] > t_us:
            b = bytes.fromhex(w["hex"])
            e = rc.decode_event(b[6:6 + int.from_bytes(b[4:6], "little")])
            if e["code"] not in ("BOOT",):
                out.append([e["code"], e["arg"]])
    return sorted(out)


def _data_since(tw, t_us: float) -> list[dict]:  # noqa: ANN001
    out = []
    for w in tw.wire_log:
        if w["dir"] == "tx" and w["type"] == rc.ASYNC["DATA"] and w["first_us"] > t_us:
            b = bytes.fromhex(w["hex"])
            out.append(rc.decode_data(b[6:6 + int.from_bytes(b[4:6], "little")]))
    return out


@pytest.mark.req("SAF-FW-015", "SAF-FW-001", "D-47")
@pytest.mark.parametrize("case", VEC["cases"], ids=[c["name"] for c in VEC["cases"]])
def test_link_watchdog_vector(twin, case):
    tw, link = twin, TwinLink(twin)
    _setup(tw, link, case)
    if case["motion_state"] != "NOT_ENABLED":
        st = link.cmd("GET_STATUS")["board_status"]["motion_state"]
        assert st == case["motion_state"], st
    _cmd(tw, link, "SET_VALID", {"valid": int(case["valid_before"])})
    t_last = max(w["last_us"] for w in tw.wire_log if w["dir"] == "rx")
    e = case["expect"]
    tw.advance_to(int((t_last + case["silence_ms"] * 1000) * 1000))
    produced = [s for s in tw.sent if s["t_us"] > t_last and s["type"] == rc.ASYNC["EVENT"]]
    if not e["trip"]:
        assert produced == [], produced
        link.send("PING")
        tw.advance_ms(60)
        assert _events_since(tw, t_last) == []
        frames = _data_since(tw, t_last)
        assert frames and all(("VALID" in d["flags"]) == case["valid_before"] for d in frames)
        assert not any("LINK_WDG" in d["status"] for d in frames)
        return
    if e["events"]:
        assert produced, "no reaction within timeout + 2 ms"
    tw.advance_ms(200)                                              # still silent: the reaction completes
    assert _events_since(tw, t_last) == e["events"]
    last = _data_since(tw, tw.now_us - 30_000)
    assert last and ("VALID" in last[-1]["flags"]) == e["valid_after"], last[-1]
    assert ("LINK_WDG" in last[-1]["status"]) == e["link_wdg_status"], last[-1]
    if e["stop"] == "controlled":
        st = [x for x in _events_since(tw, t_last) if x[0] == "STOPPED"]
        assert st == [["STOPPED", rc.STOP_CAUSE["LINK_WDG"]]]
    t_f = tw.now_us
    link.send("PING")
    tw.advance_ms(60)
    assert _events_since(tw, t_f) == e["after_frame_events"]
    last = _data_since(tw, tw.now_us - 30_000)
    assert ("LINK_WDG" in last[-1]["status"]) == e["link_wdg_after_frame"], last[-1]

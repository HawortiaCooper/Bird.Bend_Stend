"""Validator E - link watchdog in every motion state (D-47 a, ICD v0.7.4 §9.1 / §6.2, SRS v0.6.3 SAF-FW-015):
replay of the 32 cases of `vectors/linkwdg_vectors.json` on A's FW in the twin, every expectation first re-derived by
the validator's own oracle (`val_oracles/latch_ref.link_watchdog`, written from the SRS text).

Per case: the motion state is set up (NOT_ENABLED at boot, IDLE after ENABLE, MOVE_ABS / MOVE_UNTIL_LOAD at 1 mm/s),
`safety.link_timeout_ms` set, the stream on, SET_VALID(valid_before) is the LAST command frame; then the PC stays silent
for `silence_ms` (measured from the end of that frame on the wire; trip cases + 60 ms so the controlled stop ends —
the same silence period, the watchdog trips once). Observed: EVENTs (multiset, FW t_us inside the silence), the
last DATA frame before the next command frame (VALID flag, LINK_WDG status bit), the stop, and after the next frame
LINK_RESTORED / LINK_WDG.

Verifies: SAF-FW-015, SAF-FW-001 (VALID auto-clear), FW-CMD-002
TC: TC-SAF-FW-015-01 (v0.4.3 extension: every motion state)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import latch_ref as lr
import ref_codec as rc
import vhelp_m2 as m
from vhelp import V

VEC = json.loads((Path(__file__).resolve().parents[3] / "00_System" / "tools" / "vectors" /
                  "linkwdg_vectors.json").read_text(encoding="utf-8"))
CASES = VEC["cases"]


def _norm(evs) -> list[tuple[str, int]]:
    return sorted((e[0], int(e[1])) for e in evs)


def test_vectors_match_validator_oracle():
    assert VEC["icd_version"] == rc.ICD_VERSION
    assert len(CASES) == 32
    for c in CASES:
        mine = lr.link_watchdog(c["motion_state"], c["valid_before"], c["silence_ms"], c["link_timeout_ms"])
        e = dict(c["expect"])
        e["events"] = _norm(e["events"])
        e["after_frame_events"] = _norm(e["after_frame_events"])
        assert mine == e, (c["name"], mine, e)


def _setup(v: V, state: str) -> None:
    if state == "NOT_ENABLED":
        return
    if state == "IDLE":
        m.enable(v)
        return
    m.ready(v, x_um=20_000)
    if state == "MOVE_ABS":
        v.ok("MOVE_ABS", {"target_um": 200_000, "v_um_s": 1_000, "a_um_s2": 0})
    else:
        m.need(v, "MOVE_UNTIL_LOAD")
        v.ok("MOVE_UNTIL_LOAD", {"bound_um": 200_000, "v_um_s": 1_000, "a_um_s2": 0, "raw_stop": 8_000_000, "cmp": 0})
    v.advance(50)
    assert m.state(v) == state


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_linkwdg_vector_replay(v, case):
    m.need(v, "MOTION", "HOMING")
    exp = case["expect"]
    m.set_ok(v, "safety.link_timeout_ms", case["link_timeout_ms"])
    v.ok("STREAM_START")
    _setup(v, case["motion_state"])
    n0 = m.n_events(v)
    v.ok("SET_VALID", {"valid": 1 if case["valid_before"] else 0})       # the last command frame
    tw = v.tw
    last_rx = [w for w in v.wire("rx")][-1]
    assert last_rx["type"] == rc.CMD["SET_VALID"]
    t_end_world = last_rx["last_us"] + case["silence_ms"] * 1000.0 + (60_000.0 if exp["trip"] else 0.0)
    k0 = len(v.link.frames)
    tw.advance_to(int(t_end_world * 1000))
    v.link.poll()
    fw_end = tw.fw_t_us()
    data = [rc.decode_data(f.payload) for f in v.link.frames[k0:] if f.type == rc.ASYNC["DATA"]]
    assert data, "no DATA frame during the silence"
    evs = [e for e in m.events_since(v, n0) if ((fw_end - e["t_us"]) & 0xFFFFFFFF) < 0x80000000]
    got = _norm((e["code"], e["arg"]) for e in evs)
    assert got == _norm(exp["events"]), (case["name"], got)
    assert ("VALID" in data[-1]["flags"]) == exp["valid_after"], (case["name"], data[-1])
    assert ("LINK_WDG" in data[-1]["status"]) == exp["link_wdg_status"], (case["name"], data[-1])
    moving = case["motion_state"] in lr.LW_MOVING
    if exp["stop"] == "controlled":
        assert "MOVING" not in data[-1]["flags"] and "ENABLED" in data[-1]["flags"], data[-1]   # driver kept enabled
    elif moving:
        assert "MOVING" in data[-1]["flags"], data[-1]                                        # no stop below
    # the next valid command frame
    n1 = m.n_events(v)
    st = v.status()
    v.advance(20)
    after = _norm((e["code"], e["arg"]) for e in m.events_since(v, n1))
    assert after == _norm(exp["after_frame_events"]), (case["name"], after)
    # the restoring frame's own response may still show LINK_WDG (cleared while the frame is handled, order inside
    # that frame not specified, OBS-M4-01); every later DATA frame / status must not
    k2 = len(v.link.frames)
    v.advance(30)
    later = [rc.decode_data(f.payload) for f in v.link.frames[k2:] if f.type == rc.ASYNC["DATA"]]
    assert later and all(("LINK_WDG" in d["status"]) == exp["link_wdg_after_frame"] for d in later), later[-1:]
    st2 = v.status()
    assert ("LINK_WDG" in st2["status"]) == exp["link_wdg_after_frame"], st2["status"]
    assert ("VALID" in st["flags"]) == exp["valid_after"]
    if moving and not exp["trip"]:
        v.ok("STOP", {"mode": 0})

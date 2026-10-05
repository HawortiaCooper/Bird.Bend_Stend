"""Link watchdog reference model and vectors (ICD v0.7.4 §9.1, D-47 a).

Verifies: SAF-FW-015, SAF-FW-001, D-47
Run:  .venv\\Scripts\\python -m pytest 00_System/tools/tests/test_ref_linkwdg.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import ref_codec as rc  # noqa: E402
import ref_linkwdg as lw  # noqa: E402

VEC = json.loads((TOOLS / "vectors" / "linkwdg_vectors.json").read_text(encoding="utf-8"))


def test_vectors_replay_the_model_and_cover_the_matrix():
    assert VEC["icd_version"] == rc.ICD_VERSION
    assert len(VEC["cases"]) == 32 and len({c["name"] for c in VEC["cases"]}) == 32
    for c in VEC["cases"]:
        o = lw.silence(c["motion_state"], c["valid_before"], c["silence_ms"], c["link_timeout_ms"])
        e = c["expect"]
        assert (o.trip, o.stop, o.valid_after, o.link_wdg_status) == (e["trip"], e["stop"], e["valid_after"],
                                                                     e["link_wdg_status"]), c["name"]
        assert [list(x) for x in o.events] == e["events"]
        assert [list(x) for x in o.after_frame_events] == e["after_frame_events"]


def test_d47a_valid_cleared_in_every_state_stop_only_while_moving():
    for st in ("NOT_ENABLED", "ENABLING", "IDLE") + lw.MOVING_STATES:
        o = lw.silence(st, True, 1000, 1000)
        assert o.trip and not o.valid_after and ("VALID_CLEARED", 13) in o.events
        moving = st in lw.MOVING_STATES
        assert (o.stop == "controlled") == moving and o.link_wdg_status == moving
        assert (("LINK_WDG", 0) in o.events) == moving and (("STOPPED", 13) in o.events) == moving
        assert o.after_frame_events == ([("LINK_RESTORED", 0)] if moving else [])
    assert lw.silence("IDLE", False, 1000, 1000).events == []                 # VALID was 0: no EVENT
    assert lw.silence("MOVE_ABS", True, 999, 1000).trip is False               # timeout − 1 ms never trips
    assert rc.STOP_CAUSE["LINK_WDG"] == lw.CAUSE_LINK_WDG
    assert rc.MOVE_DONE_REASON.index("STOPPED") == lw.MOVE_DONE_STOPPED

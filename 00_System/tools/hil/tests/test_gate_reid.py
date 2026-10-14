"""Validator E - gate runner v1.2: re-identification after every port re-open and the C-GR-4 stop rule (reproducers
adapted from the Integrator's scratch tests, FW_code_review v1.6 §18.5). No hardware, no serial port (D-06).

    .venv\\Scripts\\python -m pytest 00_System\\tools\\hil\\tests\\test_gate_reid.py -q

Verifies: SYS-009, D-06 (C-R3: nothing sent to an unidentified device), D-57
TC: TC-SYS-009-04
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HIL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIL))
sys.path.insert(0, str(HIL / "tests"))

import hil_budget as hb  # noqa: E402
import hil_procs as P  # noqa: E402
import hil_session  # noqa: E402
from test_gate_runner import UID, today_env  # noqa: E402,F401  (fixture re-used)


# ------------------------------------------------------------------------------------------- stubs (C's)
class Tr:
    kind = "target"

    def __init__(self, log):
        self.log = log

    def close_port(self):
        self.log.append("close")

    def reopen(self):
        self.log.append("reopen")


class L:
    is_twin = False

    def __init__(self, uid_after):
        self.sent, self.uid_after = [], uid_after
        self.tr = Tr(self.sent)

    def wait(self, ms, keepalive=True, **k):
        if keepalive:
            self.sent.append("PING(keepalive)")

    def cmd(self, name, *a, **k):
        self.sent.append(name)
        return {"status": "OK"}

    def info(self):
        self.sent.append("GET_INFO")
        return {"proto_major": 1, "proto_minor": 0, "payload_version": 1, "param_dict_hash": P.DICT_HASH,
                "uid": self.uid_after, "build": "20261012-0900", "features": []}


class Op:
    item = ""

    def __getattr__(self, name):
        def f(key, *a, **k):
            if name == "ask_number":
                return P.Answer(1000.0 if key != "J-STIM" else 220.0, "operator")
            if name == "ask_yes_no":
                return P.Answer(True, "operator")
            return P.Answer("x", "operator")
        return f

    def take_log(self, _id):
        return []


def _ctx(tmp_path, uid_after):
    link = L(uid_after)
    ctx = P.Ctx(link, Op(), dict(P.DEFAULT_CFG, board_uid=UID, approved_builds={"release": "20261012-0900"}),
                tmp_path, session=P.new_session("target", UID))
    ctx.identified = True
    return ctx, link


# ------------------------------------------------------------------------------------------- re-plug blocks
def test_usb_off_block_reidentifies_before_any_command(tmp_path):
    ctx, link = _ctx(tmp_path, UID)
    it = P.run_item(ctx, "HG-01", "board", P.hg01)
    assert it.error == "" and ctx.identified
    k = link.sent.index("reopen")
    assert link.sent[:k] == ["close"]                       # nothing sent while unplugged
    assert link.sent[k + 1:k + 3] == ["PING", "GET_INFO"]   # only PING before the identity check


def test_c_gr_4_wrong_board_after_replug_stops_the_session(tmp_path):
    """A different board plugged back inside HG-01: identity refused, the item ends unidentified, and the session rule
    stops (after the item) and refuses every further non-identity item (before it), C-GR-4."""
    ctx, link = _ctx(tmp_path, "0" * 24)
    it = P.run_item(ctx, "HG-01", "board", P.hg01)
    assert it.error.startswith("REFUSED") and not ctx.identified
    assert "HALT" not in link.sent and "HALT_CLEAR" not in link.sent          # C-R3
    assert hil_session.stop_reason("HG-01", it) is None                       # not an identity item ...
    assert hil_session.unidentified_reason(ctx, "HG-01", before=False)        # ... but the new rule stops
    assert hil_session.unidentified_reason(ctx, "HG-19", before=True)         # and nothing else may start
    assert hil_session.unidentified_reason(ctx, "S-ID", before=True) is None  # an identity item may
    ctx.identified = True
    assert hil_session.unidentified_reason(ctx, "HG-19", before=True) is None


def test_c_gr_5_hg15_power_cycle_reidentifies():
    """HG-15 POWER: unidentified before the port is closed, PING + check_identity() right after the re-open."""
    src = inspect.getsource(P.hg15)
    i = src.index('method == "POWER"')
    block = src[i:src.index("want = \"POWER_ON\"", i)]
    assert block.index("ctx.identified = False") < block.index("close_port()")
    assert block.index("reopen()") < block.index('cmd("PING")') < block.index("check_identity(ctx)")


# ------------------------------------------------------------------------------------------- session loop
class FakeTr:
    kind = "target"

    def __init__(self, *a, **k):
        self.t = 0

    def now_ns(self):
        self.t += 1000
        return self.t

    def write(self, b):
        pass

    def read(self):
        return b""

    def close(self):
        pass

    def close_port(self):
        pass

    def reopen(self):
        pass


def test_session_loop_stops_after_an_item_that_loses_the_identity(monkeypatch, capsys, today_env):
    """End to end through hil_session.main (fake transport, scripted items): S-00 identifies, HG-01 ends with the board
    unidentified -> the session stops with rc 2; HG-19 never runs, no BENCH-EXIT, the manual restore is printed."""
    ran = []

    def fake_run_item(ctx, item_id, title, fn, req="", method=""):
        ran.append(item_id)
        it = ctx.begin(item_id, title, req, method)
        if item_id == "S-00":
            ctx.identified = True
            ctx.check(hb.check_true("identity", True, "scripted"))
        elif item_id == "HG-01":
            ctx.identified = False
            it.error = "REFUSED: board UID differs after the re-plug (scripted)"
        else:
            ctx.check(hb.check_true("ran", True, "scripted"))
        return ctx.end()

    monkeypatch.setattr(hil_session, "SerialTransport", FakeTr)
    monkeypatch.setattr(hil_session.P, "run_item", fake_run_item)
    out = today_env["tmp"] / "s"
    rc = hil_session.main(["--port", "COM7", "--approval", str(today_env["file"]), "--delivery",
                           str(today_env["delivery"]), "--out", str(out), "--only", "HG-01,HG-19", "--no-export"])
    err = capsys.readouterr().err
    assert rc == 2 and ran == ["S-00", "HG-01"], ran
    assert "not identified" in err and "by hand" in err and "BENCH-EXIT" not in ran


def test_session_loop_refuses_to_start_an_item_while_unidentified(monkeypatch, capsys, today_env):
    """Belt and braces: if an item ends unidentified but WITHOUT an error (e.g. a future procedure forgets to raise),
    the next non-identity item is still refused before anything is sent."""
    ran = []

    def fake_run_item(ctx, item_id, title, fn, req="", method=""):
        ran.append(item_id)
        it = ctx.begin(item_id, title, req, method)
        ctx.identified = item_id == "S-00"
        ctx.check(hb.check_true("ran", True, "scripted"))
        return ctx.end()

    monkeypatch.setattr(hil_session, "SerialTransport", FakeTr)
    monkeypatch.setattr(hil_session.P, "run_item", fake_run_item)
    monkeypatch.setattr(hil_session, "unidentified_reason",
                        lambda ctx, item_id, before: (None if not before else
                                                      hil_session.__dict__["_orig_unid"](ctx, item_id, before=True)))
    hil_session.__dict__["_orig_unid"] = _ORIG_UNID
    out = today_env["tmp"] / "s2"
    rc = hil_session.main(["--port", "COM7", "--approval", str(today_env["file"]), "--delivery",
                           str(today_env["delivery"]), "--out", str(out), "--only", "HG-01,HG-19", "--no-export"])
    err = capsys.readouterr().err
    assert rc == 2 and ran == ["S-00", "HG-01"] and "before HG-19" in err, (ran, err)


_ORIG_UNID = hil_session.unidentified_reason


def test_twin_sessions_are_not_affected(tmp_path):
    ctx = SimpleNamespace(twin=True, identified=False)
    assert hil_session.unidentified_reason(ctx, "HG-19", before=True) is None
    assert hil_session.unidentified_reason(ctx, "HG-19", before=False) is None
    assert json.dumps(hil_session.MANUAL_RESTORE)


# ------------------------------------------------------------------------------------------- O-GR-6 restore text
def _first_off(text: str) -> int:
    t = text.lower()
    hits = [t.find(x) for x in ("48 v off", "48 v psu off", "switch the 48 v off") if t.find(x) >= 0]
    return min(hits) if hits else -1


def test_loaded_restore_releases_the_load_before_48v_off():
    """O-GR-6: in phase 4 (or with the spring fitted) the manual restore keeps the driver holding (48 V ON, USB
    unplugged) and releases the load mechanically BEFORE the 48 V goes off; never '48 V OFF' first."""
    ctx = SimpleNamespace(shared={})
    for phase in (4, 5):
        t = hil_session.manual_restore(ctx, phase)
        low = t.lower()
        assert "keep the 48 v on" in low and "release the load" in low
        assert _first_off(t) > low.index("release the load") > low.index("keep the 48 v on")
    ctx.shared["load_applied"] = True                       # spring fitted earlier than phase 4
    assert hil_session.manual_restore(ctx, 2) == hil_session.MANUAL_RESTORE_LOADED
    ctx.shared["load_applied"] = False                      # removed again (HG-25 NOSPEC), unloaded phase
    t = hil_session.manual_restore(ctx, 1)
    assert t == hil_session.MANUAL_RESTORE and _first_off(t) >= 0


def test_session_stop_in_phase_4_prints_the_loaded_restore(monkeypatch, capsys, today_env):
    """End to end: HG-15 (phase 4) ends with the device unidentified (e.g. a wrong board after the power cycle) - the
    session stops and the printed restore is the loaded one."""
    ran = []

    def fake_run_item(ctx, item_id, title, fn, req="", method=""):
        ran.append(item_id)
        if item_id == "S-00":                               # scripted identity: image verified in this session
            ctx.image = "meas"
            ctx.session["images"]["meas"] = "scripted|" + UID
        it = ctx.begin(item_id, title, req, method)
        if item_id == "HG-15":
            ctx.identified = False
            it.error = "REFUSED: board UID differs after the power cycle (scripted)"
        else:
            ctx.identified = True
            ctx.check(hb.check_true("ran", True, "scripted"))
        return ctx.end()

    monkeypatch.setattr(hil_session, "SerialTransport", FakeTr)
    monkeypatch.setattr(hil_session.P, "run_item", fake_run_item)
    rc = hil_session.main(["--port", "COM7", "--approval", str(today_env["file"]), "--delivery",
                           str(today_env["delivery"]), "--out", str(today_env["tmp"] / "s4"), "--only", "HG-15,HG-25",
                           "--no-export"])
    err = capsys.readouterr().err
    assert rc == 2 and ran[-1] == "HG-15" and "HG-25" not in ran, ran
    restore = err[err.index("Make the bench safe"):]
    assert "keep the 48 V ON" in restore and _first_off(restore) > restore.lower().index("release the load")

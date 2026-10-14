"""Validator F — child process of TC-SW-BRU-002-02 (kill-and-restart of a bring-up session).

Runs the application's backend on the real clock with the in-process simulator (as the packaged application does with
``--sim``), opens a bring-up session, commits operator entries, then brings the session to the requested kill point
and writes a marker (what was committed). The parent test kills this process by its PID and resumes the session in a
fresh backend. Not a test module (no ``test_`` prefix); started only by ``test_v_bringup.py``.

Usage: ``python bru_child.py <phase> <steps.json> <marker.json> <recordings root>``;
phase = entry | auto | motion | save | package.

Verifies: (infrastructure of TC-SW-BRU-002-02)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _fill(sd: dict) -> dict:
    out = {}
    for f in sd.get("fields", ()):
        if f["kind"] == "confirm":
            out[f["name"]] = True
        elif f["kind"] == "text":
            out[f["name"]] = "F child"
        elif f["kind"] == "choice":
            out[f["name"]] = (f.get("pass_values") or f["choices"])[0]
        else:
            lo, hi = f.get("min"), f.get("max")
            v = (lo + hi) / 2 if lo is not None and hi is not None else lo + 1 if lo is not None else \
                hi / 2 if hi is not None else 1.0
            out[f["name"]] = round(float(v), int(f.get("decimals", 2)))
    return out


def _wait(pred, be, timeout_s: float = 60.0) -> bool:
    t_end = time.monotonic() + timeout_s
    while time.monotonic() < t_end:
        be.gui_beat()
        if pred():
            return True
        time.sleep(0.02)
    return bool(pred())


def main() -> int:
    phase, steps, marker, rec = sys.argv[1:5]
    from bend_stand.core.backend import Backend, BackendSettings
    from bend_stand.core.testing import isolated_settings          # data-safety rule (incident 2026-10-14)

    be = Backend(BackendSettings(**isolated_settings(recordings_root=rec, bringup_steps=steps, hotkey="off")))
    be.start()
    be.connect_async("sim").result(15)
    assert _wait(lambda: be.status().stream.on and be.status().board is not None, be, 15)
    bu = be.bringup
    g = bu.start(confirmed=True)
    assert g.ok, g
    doc = json.loads(Path(steps).read_text(encoding="utf-8"))
    meas = [s for s in doc["steps"] if s["kind"] == "measure"][:2]
    entries, states = {}, {}
    for s in meas:
        issues = bu.submit(s["id"], _fill(s), "child")
        assert not [i for i in issues if str(i.severity.value).upper() == "ERROR"], issues
        entries[s["id"]] = bu.step(s["id"]).entries
        states[s["id"]] = bu.step(s["id"]).state
    info = next(s["id"] for s in doc["steps"] if s["kind"] == "info")
    assert bu.mark(info, "PASS", remark="child").ok
    mk = {"session_id": bu.status().session_id, "entries": {k: dict(v) for k, v in entries.items()},
          "states": states, "marked": [info], "pid": os.getpid()}
    if phase == "auto":
        bu.select("7.1")
        assert _wait(lambda: bu.step("7.1").auto is not None and bu.step("7.1").auto.state == "WAITING", be, 10)
        mk["auto_step"] = "7.1"
    elif phase == "motion":
        assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
        assert be.motion.enable().ok
        assert _wait(lambda: be.status().motion.enabled, be, 5)
        be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}).result(10)
        fut = be.motion.home(load_confirmed=True)
        assert _wait(fut.done, be, 60)                  # GUI beats while it runs (liveness, SWR-01)
        assert _wait(lambda: be.status().motion.homed and not be.status().motion.moving, be, 10)
        pid = next(s["id"] for s in doc["steps"] if s["kind"] == "pulse_integrity")
        bu.select(pid)
        assert bu.run_action(pid, confirmed=True).ok
        assert _wait(lambda: "round trip" in bu.step(pid).action_text, be, 90)
        assert _wait(lambda: be.status().motion.moving, be, 10)
        mk["pulse"] = pid
    elif phase == "save":
        be.config.save_async()
    elif phase == "package":
        bu.create_package_async()
    tmp = Path(marker).with_suffix(".tmp")
    tmp.write_text(json.dumps(mk), encoding="utf-8")
    os.replace(tmp, marker)
    t_end = time.monotonic() + 300
    while time.monotonic() < t_end:                     # keep running (GUI beat) until the parent kills us
        be.gui_beat()
        time.sleep(0.02)
    return 3


if __name__ == "__main__":
    sys.exit(main())

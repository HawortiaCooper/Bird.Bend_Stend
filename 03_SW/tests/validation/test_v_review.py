"""Validator F — whole-codebase code review of the PC software (Task 6), reproducers of ``SW_code_review.md``.

Every ``SWR-nn`` finding that can be reproduced in-process has one test here. Tests of open findings are
``xfail(strict=True)``: they assert the behaviour the SRS / SW_design asks for and fail while the finding is open;
when the owner fixes it the test XPASSes, the strict marker turns that into a failure, and the marker is removed in
the fix (the test then guards it). A few plain tests record review areas that were checked and found correct
(evidence). v1.2 (§8 fix verification): the 16 original reproducers guard the fixes (markers removed by the owners);
the second block adds F's independent tests of fixes that had only the implementer's tests (SWR-15, 22, 23, 24, 25,
28, 14 incident log) and the reproducers of the new findings SWR-29 … SWR-32.

Unlike the milestone suites, several reproducers drive single backend units directly (``Pipeline``, ``Recorder``,
``CommandChannel``, ``GlobalHaltHotkey``, the file loaders) or patch one function to force a thread interleaving
deterministically: the defects are in those units and cannot be reached through the public API in a deterministic
way. Expected values still come from the SRS / SW_design text, never from the code under test.

Anchor: commit e600169 (file:line references in ``03_SW/docs/SW_code_review.md``).

Verifies: SAF-SW-001, SAF-SW-003, SAF-SW-005, SW-STOP-001, SW-STOP-002, SW-STOP-003, SW-SEQ-004, SW-SEQ-006,
SW-ACQ-002, SW-ACQ-004, SW-LIM-001, SW-LIM-003, SW-LIM-004, SW-CFG-003, SW-REP-001, SW-REP-003, SW-SEQF-001,
SW-CAL-009, SW-META-001, IF-003, IF-004, IF-006, IF-011, SW-CFG-001, SW-ACQ-003, SW-SEQ-002, SW-PLT-001
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from pathlib import Path

import pytest

import harness as H

MS = 1_000_000


def _data_dir() -> Path:
    """The per-test application data folder (root conftest ``_private_data_dir``)."""
    return Path(os.environ["BEND_STAND_DATA_DIR"])


def _write_session(doc: dict) -> None:
    p = _data_dir() / "sessions" / "default.bbsession.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": "bird.bend.session", "schema_version": 1, **doc}), encoding="utf-8")


def _codes(g) -> set[str]:
    return {str(i.code) for i in g.items}


# ============================================================================================ SWR-01 liveness

@pytest.mark.req("SAF-SW-001", "SAF-SW-003")
@pytest.mark.defect("SWR-01")
def test_swr01_pipeline_stall_while_moving_sends_stop(lockstep):
    """SW_design §4.6: 'DATA received but not evaluated > 200 ms … while moving → priority STOP + terminate_all'.
    SAF-SW-001 needs every received DATA frame evaluated (STOP ≤ 50 ms after the violating frame). Stimulus: the
    Pipeline thread (where the SafetySupervisor runs) is stalled for 1.5 s during a 100 mm move while the Reader keeps
    receiving DATA. Expected: a STOP on the wire ≤ 300 ms after the stall began. Observed: none — only PING /
    GET_STATUS are written, the move continues unsupervised."""
    # Verifies: SAF-SW-001, SAF-SW-003
    be = lockstep()
    H.m2_ready(be)
    H.move_to(be, 100.0, speed_mm_s=5.0)
    H.advance(be, 500)
    assert H.motion(be).moving
    m0, t0 = H.wire_mark(be), H.now_ns(be)
    H.stall(be, "pipeline", 1500)
    H.advance(be, 1500)
    stops = [w for w in H.tx(be, "STOP", since=m0) if w.t_ns - t0 <= 300 * MS]
    assert stops, sorted({w.name for w in H.tx(be, since=m0)})


# ============================================================================================ SWR-02 pipeline batch

@pytest.mark.req("SAF-SW-001", "SW-STOP-003", "IF-006")
@pytest.mark.defect("SWR-02")
def test_swr02_handler_exception_does_not_drop_following_frames():
    """IF-006 / SAF-SW-001: every received DATA frame is processed; SW-STOP-003: an ESTOP / HALT EVENT terminates
    operations within one frame. Stimulus: three async frames queued (EVENT LIMIT_SET, DATA, EVENT ESTOP_SET); the
    FW-event handler raises once (any defect in Device / Motion / Backend reactions). Expected: the DATA frame and the
    second EVENT are still processed. Observed: ``Pipeline.step`` aborts the 64-frame batch — both are lost."""
    # Verifies: SAF-SW-001, SW-STOP-003, IF-006
    from bend_stand.core import protocol_gen as pg
    from bend_stand.core.clock import LockstepClock
    from bend_stand.core.events import EventBus
    from bend_stand.core.liveness import LivenessMonitor
    from bend_stand.core.pipeline import Pipeline
    from bend_stand.io import protocol as P
    from bend_stand.io.framing import Frame

    clk = LockstepClock()
    seen: list[int] = []

    def on_ev(ev):
        seen.append(int(ev.code))
        if len(seen) == 1:
            raise RuntimeError("handler defect")

    p = Pipeline(clk, EventBus(clk), LivenessMonitor(clk), on_fw_event=on_ev)
    ev1 = P.encode_event(P.EventPayload(1000, int(pg.Event.LIMIT_SET), 0, 0, 0))
    ev2 = P.encode_event(P.EventPayload(2000, int(pg.Event.ESTOP_SET), 0, 0, 0))
    data = P.encode_data(P.DataSample(1500, pg.PAYLOAD_VERSION, 0, 100, 0, 1, 0))
    p.put(Frame(int(pg.AsyncType.EVENT), 1, ev1, 1))
    p.put(Frame(int(pg.AsyncType.DATA), 0, data, 2))
    p.put(Frame(int(pg.AsyncType.EVENT), 2, ev2, 3))
    for t in (0, 1):
        try:
            p.step(t)
        except RuntimeError:
            pass                                      # the threaded loop logs and continues the same way
    assert p.counters.data_frames == 1 and int(pg.Event.ESTOP_SET) in seen, (p.counters, seen)


# ============================================================================================ SWR-03 limits off

@pytest.mark.req("SAF-SW-001", "SW-LIM-004", "SW-LIM-003")
@pytest.mark.defect("SWR-03")
def test_swr03_saved_limits_off_do_not_bypass_load_input_rule(lockstep):
    """SAF-SW-001 (last sentence): 'the only way to move without valid load limits is the no-specimen mode'; SW_design
    B5-01: switching a load limit off while the input is invalid is refused. Stimulus: the default session file has
    both PC load limits off (saved earlier with a valid input); application start → no tare (session-only) → load
    input invalid. Expected: the motion gate refuses with LOAD_INPUT_INVALID (or the limits are re-enabled).
    Observed: no refusal, no no-specimen banner; ENABLE + HOME + MOVE run without any PC load limit."""
    # Verifies: SAF-SW-001, SW-LIM-004, SW-LIM-003
    _write_session({"limits": {"pull_enabled": False, "push_enabled": False}})
    be = lockstep()
    sf = H.safety(be)
    assert not sf.load_input_valid and not sf.no_specimen_mode
    assert "LOAD_INPUT_INVALID" in _codes(H.gate(be, "MOVE")), H.gate(be, "MOVE")


# ============================================================================================ SWR-04 VALID left on

@pytest.mark.req("SW-SEQ-004")
@pytest.mark.defect("SWR-04")
def test_swr04_refused_ramp_step_does_not_leave_valid_on(lockstep):
    """SW-SEQ-004: VALID = 0 at window end and on pause/stop/abort; every VALID = 1 frame inside a planned window.
    Stimulus: TRAVEL step with ``capture_during_move`` (SET_VALID 1 is sent before the move); the FW refuses the
    MOVE_ABS (injected NACK E_STATE). Expected: the sequence ends and VALID is 0 afterwards. Observed: the run ends
    STOPPED (STEP_REFUSED) and VALID stays 1 on the board — every later DATA frame carries VALID = 1."""
    # Verifies: SW-SEQ-004
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 20.0, speed_mm_s=5.0, capture_during_move=True, capture_s=1.0)],
                     travel_ref="machine")
    H.inject_nack(be, "MOVE_ABS", "E_STATE", 1 << 6, 1)
    m0 = H.wire_mark(be)
    assert H.seq_start(be, seq).ok
    st = H.seq_wait_end(be)
    assert st.state == "STOPPED" and st.end_reason == "STEP_REFUSED", st
    assert [w.fields.get("valid") for w in H.tx(be, "SET_VALID", since=m0)][:1] == [1]
    H.advance(be, 2000)
    assert "VALID" not in H.rx(be, "DATA")[-1].fields["flags"]


# ============================================================================================ SWR-05 recorder race

@pytest.mark.req("SW-ACQ-002", "SW-ACQ-004")
@pytest.mark.defect("SWR-05")
def test_swr05_rows_during_stop_never_leak_into_next_recording(tmp_path, monkeypatch):
    """SW-ACQ-002: every frame exactly once; SW-ACQ-004: never drop rows silently. Interleaving forced
    deterministically: the Pipeline thread delivers one row while ``stop()`` writes the sidecar (after its final
    drain, before ``state = IDLE``). Expected: the row is written to recording 1 or counted as lost there, and
    recording 2 contains only its own rows. Observed: recording 1 says complete / 0 lost, the row stays in the queue
    (``start()`` does not clear it) and is written as the first D row of recording 2."""
    # Verifies: SW-ACQ-002, SW-ACQ-004
    from bend_stand.core import recorder as R
    from bend_stand.core.clock import LockstepClock
    from bend_stand.core.pipeline import DataRow

    clk = LockstepClock()
    rec = R.Recorder(clk)
    rec.free_space_probe = lambda _p: 10 ** 12

    def row(i: int) -> DataRow:
        return DataRow(i, i * 12_500, i * 12_500, i * 0.0125, i & 0xFFFF, 0, 4, 0, 100, 0, 0, 0, 0)

    f1 = rec.start(tmp_path, {})
    for i in range(5):
        rec.on_row(row(i))
    rec.step()
    orig = R.atomic_write_json

    def racing(path, obj):
        if rec.state == "RECORDING":
            rec.on_row(row(99))
        return orig(path, obj)

    monkeypatch.setattr(R, "atomic_write_json", racing)
    rec.stop()
    monkeypatch.setattr(R, "atomic_write_json", orig)
    lost1 = rec.rows_lost
    clk.advance(ns=2 * 1_000_000_000)
    f2 = rec.start(tmp_path, {})
    rec.on_row(row(200))
    rec.step()
    rec.stop()

    def t_rows(folder: Path) -> list[int]:
        return [int(ln.split(",")[2]) for ln in (folder / "data.csv").read_text().splitlines() if ln.startswith("D,")]

    r1, r2 = t_rows(f1), t_rows(f2)
    assert r2 == [200 * 12_500], (r1, r2)
    assert 99 * 12_500 in r1 or lost1 == 1, (r1, lost1)


# ============================================================================================ SWR-06 session types

@pytest.mark.req("SW-LIM-003")
@pytest.mark.defect("SWR-06")
def test_swr06_session_wrong_type_does_not_prevent_start():
    """SW-LIM-003 (limits applied at start from the session file) and SW_design §13.5 (broken file → defaults +
    log). Stimulus: ``limits.pull_trip_n`` is the string "100" in the default session file. Expected: the backend
    starts with the default limits and reports the file. Observed: ``Backend()`` raises TypeError — the application
    cannot start until the file is deleted by hand."""
    # Verifies: SW-LIM-003
    _write_session({"limits": {"pull_trip_n": "100"}})
    Backend, BackendSettings = H.backend_cls()
    be = Backend(BackendSettings(clock="lockstep", test_hooks=True))
    assert be.session.get().limits.pull_trip_n == pytest.approx(1961.33)


@pytest.mark.req("SW-LIM-003", "SW-LIM-001")
@pytest.mark.defect("SWR-06")
def test_swr06_session_string_travel_limit_does_not_break_status(lockstep):
    """Stimulus: ``limits.travel_max_mm`` is the string "5" (enabled) in the default session file. The start path
    (``SessionAPI.load_default``) runs no type / LimitsAPI check. Expected: the value is refused at load (defaults or
    the limit off + issue) and ``status()`` works. Observed: ``status()`` raises TypeError on every call (GUI
    refresh fails; the SafetySupervisor travel rule raises per frame)."""
    # Verifies: SW-LIM-003, SW-LIM-001
    _write_session({"limits": {"travel_max_mm": "5", "travel_max_enabled": True}})
    be = lockstep(connect=False)
    st = be.status()
    assert st is not None


# ============================================================================================ SWR-07 run log lost

@pytest.mark.req("SW-ACQ-002", "SW-REP-001", "SW-REP-003")
@pytest.mark.defect("SWR-07")
def test_swr07_shutdown_mid_sequence_keeps_the_run_log(lockstep):
    """SW-ACQ-002 (recording during the whole sequence), SW-REP-001/003 (JSON metadata with sequence + events; report
    reproducible offline). Stimulus: a 2-step sequence with one finished capture window; the application closes
    (``Backend.shutdown()``, C-09 confirmed). Expected: ``meta.json`` contains the (partial) ``sequence_runs`` entry and
    the integrity block does not claim a complete run. Observed: ``recorder.stop()`` runs first, the run log appended
    later is never written; ``meta.json`` has no ``sequence_runs`` and ``integrity.complete = true``."""
    # Verifies: SW-ACQ-002, SW-REP-001, SW-REP-003
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 10.0, speed_mm_s=5.0, settle_s=0.2, capture_s=1.0),
                      H.step("travel", 20.0, speed_mm_s=5.0, settle_s=0.2, capture_s=1.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    assert H.seq_until(be, lambda s: s.windows_done >= 1 and s.exec_idx == 1)
    folder = H.seq_status(be).recording_folder
    be.shutdown()
    m = H.meta(folder)
    assert m.get("sequence_runs"), sorted(m)


# ============================================================================================ SWR-08 config gate

@pytest.mark.req("SW-CFG-003", "SW-SEQ-003")
@pytest.mark.defect("SWR-08")
def test_swr08_config_write_refused_while_sequence_paused(lockstep):
    """SW_design §5.6 gate table: ``config_write`` REFUSE when 'any operation running'. ``motion.steps_per_mm`` (not
    moving_ok, applied at once, every µm position rescales) written during a PAUSED sequence would move the remaining
    absolute targets and the soft limits physically. Expected: the gate refuses and no SET_PARAM reaches the wire.
    Observed: the gate has only a WARN for MOVING; ``write_and_verify_async`` applies no gate at all."""
    # Verifies: SW-CFG-003, SW-SEQ-003
    be = lockstep()
    H.m2_ready(be)
    seq = H.sequence([H.step("travel", 40.0, speed_mm_s=5.0, settle_s=0.2, capture_s=1.0)], travel_ref="machine")
    assert H.seq_start(be, seq).ok
    H.seq_until(be, lambda s: s.phase in ("MOVING", "COMMAND"))
    H.pause(be)
    assert H.seq_until(be, lambda s: s.state == "PAUSED", 3000)
    H.advance(be, 1500)
    g = H.gate(be, "CONFIG_WRITE")
    m0 = H.wire_mark(be)
    fut = H.write_verify(be, {"motion.steps_per_mm": 4000.0})
    H.advance(be, 1000)
    sent = [w for w in H.tx(be, "SET_PARAM", since=m0)]
    del fut
    assert not g.ok and not sent, (g, [w.fields for w in sent])


# ============================================================================================ SWR-09 hotkey liveness

@pytest.mark.req("SW-STOP-002", "SAF-SW-005")
@pytest.mark.defect("SWR-09")
@pytest.mark.rt
def test_swr09_hung_hotkey_thread_is_not_shown_active():
    """SW-STOP-002: the GUI shows whether the Pause/Break key is active; SW_design §4.6: 'the hotkey thread missing
    its 250 ms ping → warning "Pause/Break key unavailable"'. Stimulus: the (fake) hotkey thread hangs in its message
    loop; the Supervisor keeps pinging for 1.5 s. Expected: hotkey status no longer REGISTERED / LL_HOOK. Observed:
    still REGISTERED (``last_beat_ns`` is stamped but never read)."""
    # Verifies: SW-STOP-002, SAF-SW-005
    be = H.realtime_backend(hotkey="fake")
    try:
        assert H.wait_rt(lambda: be.hotkey_status().mode == "REGISTERED", 5.0)
        be.hotkey.backend.stall()
        time.sleep(1.5)
        mode = be.hotkey_status().mode
    finally:
        hk = be.hotkey
        if hk is not None:
            hk.backend.unstall()
        be.shutdown()
    assert mode not in ("REGISTERED", "LL_HOOK"), mode


# ============================================================================================ SWR-10 priority race

@pytest.mark.req("SW-STOP-001", "IF-011")
@pytest.mark.defect("SWR-10")
def test_swr10_fast_ack_does_not_turn_a_written_stop_into_not_sent():
    """SW-STOP-001 / IF-011 (priority path, result reported to the operator). ``CommandChannel.send_priority`` finds the
    write time by searching ``_inflight`` after ``submit()`` released the lock; if the Reader thread has already
    matched the ACK (fast link, GIL switch), the request is gone and ``t = None`` → ``Device._priority_stop`` reports
    'not written' (``StopResult.sent = False``) for a STOP that was written and acknowledged. Interleaving forced:
    the ACK is delivered right after ``submit()`` returns."""
    # Verifies: SW-STOP-001, IF-011
    from bend_stand.core import protocol_gen as pg
    from bend_stand.core.link import CommandChannel, FrameWriter
    from bend_stand.io.framing import Frame

    class _Tr:
        def __init__(self) -> None:
            self.frames: list[bytes] = []

        def write(self, b: bytes) -> None:
            self.frames.append(bytes(b))

    w = FrameWriter(_Tr())
    ch = CommandChannel(w, seed=1)
    written: list[tuple[int, int]] = []
    w.on_write = lambda ftype, seq, _p, _t: written.append((ftype, seq))
    orig = ch.submit

    def racing_submit(cmd, payload=b"", **kw):
        fut = orig(cmd, payload, **kw)
        ftype, seq = written[-1]
        ch.on_response(Frame(ftype | pg.RESP_BIT, seq, b"\x00", 1))      # Reader thread wins the race
        return fut

    ch.submit = racing_submit                                             # type: ignore[method-assign]
    fut, t_write, err = ch.send_priority(int(pg.Cmd.STOP), bytes([0]))
    assert written and fut.done() and fut.exception() is None and err is None
    assert t_write is not None


# ============================================================================================ SWR-11 report HTML

@pytest.mark.req("SW-REP-001")
@pytest.mark.defect("SWR-11")
def test_swr11_report_html_escapes_sidecar_values(tmp_path):
    """SW-REP-001 / SW-REP-003 (report rebuilt offline from a recording, possibly from another PC). Stimulus: a
    ``meta.json`` whose run-log result has ``n = "<script>…</script>"`` (and markup in marks, label, name). Expected:
    no raw markup from the recording in ``report.html``. Observed: marks / label / name are escaped, but ``r.n``,
    ``r.exec_idx`` and ``r.step_idx`` are interpolated raw (``StepResult`` fields are not type-coerced from JSON)."""
    # Verifies: SW-REP-001, SW-REP-003
    from bend_stand.core.recorder import COLUMNS
    from bend_stand.core.report import build_report

    res = {"exec_idx": 0, "step_idx": 0, "uid": "u1", "label": "<b>L</b>", "kind": "travel",
           "n": "<script>alert(1)</script>", "flags": ["NOT_REACHED"]}
    (tmp_path / "meta.json").write_text(json.dumps({
        "schema": "bird.bend.recording", "schema_version": 1,
        "marks_final": {"specimen": "<img src=x onerror=alert(2)>", "custom": [["<k>", "<v>"]]},
        "sequence_runs": [{"sequence": {"name": "<i>n</i>"}, "state": "STOPPED", "windows": [], "results": [res]}]}),
        encoding="utf-8")
    (tmp_path / "data.csv").write_text("# x\n" + ",".join(COLUMNS) + "\n", encoding="utf-8")
    html = Path(build_report(tmp_path).html).read_text(encoding="utf-8")
    assert "<img" not in html and "<b>L" not in html and "<i>n" not in html     # escaped today
    assert "<script>" not in html


@pytest.mark.req("SW-REP-001", "SW-META-001")
def test_swr_evidence_report_escapes_operator_marks(tmp_path):
    """Evidence (no finding): operator-entered marks, custom fields, step labels and the sequence name are
    HTML-escaped in ``report.html`` (``html.escape``)."""
    # Verifies: SW-REP-001, SW-META-001
    from bend_stand.core.recorder import COLUMNS
    from bend_stand.core.report import build_report

    (tmp_path / "meta.json").write_text(json.dumps({
        "schema": "bird.bend.recording", "schema_version": 1,
        "marks_final": {"specimen": "<img src=x onerror=alert(2)>", "number": "\"'&<>", "operator": "<svg/onload=1>",
                        "notes": "</td><script>x</script>", "custom": [["<k>", "<v onclick=1>"]]}}), encoding="utf-8")
    (tmp_path / "data.csv").write_text("# x\n" + ",".join(COLUMNS) + "\n", encoding="utf-8")
    html = Path(build_report(tmp_path).html).read_text(encoding="utf-8")
    for bad in ("<img", "<svg/", "<script>", "<k>", "<v onclick"):
        assert bad not in html, bad


# ============================================================================================ SWR-12 loaders

@pytest.mark.req("SW-SEQF-001")
@pytest.mark.defect("SWR-12")
def test_swr12_sequence_file_deep_nesting_is_a_file_error(tmp_path):
    """SW-SEQF-001: an invalid file gives an error and leaves the current sequence unchanged; the loader contract is
    ``FileFormatError`` on any error. Stimulus: a 100 000-deep JSON array. Observed: ``RecursionError`` escapes
    (same in ``schema.read_json`` for session / calibration / board-config / presets and in the report reader)."""
    # Verifies: SW-SEQF-001
    from bend_stand.core.errors import FileFormatError
    from bend_stand.core.sequencer import seqfile

    p = tmp_path / "deep.bbseq.json"
    p.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(FileFormatError):
        seqfile.load(p)


@pytest.mark.req("SW-CAL-009")
@pytest.mark.defect("SWR-12")
def test_swr12_corrupt_active_calibration_does_not_prevent_start():
    """SW-CAL-009 (active calibration loaded at start; an unreadable file never becomes active and is reported).
    Stimulus: ``calibration/active_load.json`` is a 100 000-deep JSON array. Expected: start without a calibration
    (reason shown). Observed: ``RecursionError`` from ``Backend()`` (only ``FileFormatError`` is caught)."""
    # Verifies: SW-CAL-009
    p = _data_dir() / "calibration" / "active_load.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    Backend, BackendSettings = H.backend_cls()
    be = Backend(BackendSettings(clock="lockstep", test_hooks=True))
    assert be.load_input.cal is None


# ============================================================================================ SWR-13 NaN target

@pytest.mark.req("SW-LIM-001")
@pytest.mark.defect("SWR-13")
def test_swr13_nan_target_is_refused_by_the_gate(lockstep):
    """SW-LIM-001 (targets outside the range refused with a message) and the MotionController contract (refusals as a
    ``GateRefused`` future, nothing sent, never raises). Stimulus: ``check(MOVE, target_mm=nan)`` and
    ``move_to(nan)``. Observed: the check is OK (NaN compares False with both range ends); ``move_to`` raises
    ``ValueError('cannot round nan')`` into the caller."""
    # Verifies: SW-LIM-001
    be = lockstep()
    H.m2_ready(be)
    assert not H.motion_check(be, "MOVE", target_mm=float("nan")).ok
    fut = H.move_to(be, float("nan"))
    assert fut.done() and fut.exception() is not None


# ============================================================================================ SWR-16 pull direction

@pytest.mark.req("SW-SEQ-006", "SW-SEQ-005")
@pytest.mark.defect("SWR-16")
def test_swr16_sequence_pull_dir_mismatch_is_flagged(lockstep):
    """SW-SEQ-006: the approach direction comes from ``pull_dir`` and the error sign. The executor uses the
    *sequence's* ``pull_dir`` (file / sequence tab), the SafetySupervisor and the motion trip-direction rule use the
    *session's*. Measured during the review (spring both sides, k = 50 N/mm, target +100 N): with the sequence
    ``pull_dir = −1`` against the session's +1 the axis loads the specimen in compression until the SLIP guard trips
    at −36 N. Review expectation (owner decision): ``check_start`` flags the mismatch (REFUSE or CONFIRM)."""
    # Verifies: SW-SEQ-006, SW-SEQ-005
    be = lockstep()
    assert be.session.get().pull_dir == 1
    seq = H.sequence([H.step("load", 100.0, speed_mm_s=2.0, tol_n=2.0, settle_s=0.2, capture_s=0.5)],
                     travel_ref="machine", pull_dir=-1)
    g = be.sequencer.check_start(seq)
    assert any("pull" in i.text.lower() or "PULL_DIR" in str(i.code) for i in g.items), g


# ============================================================================================ evidence: framing

@pytest.mark.req("IF-003", "IF-004")
def test_swr_evidence_decoder_bounded_under_adversarial_input():
    """Evidence (no finding): the ICD §2.3 receiver never raises and its buffer stays bounded (≤ one read chunk + one
    maximal frame) under 2 MB of adversarial input — random bytes rich in ``A5 5A`` sync pairs, LEN values around the
    160 limit, truncated frames and valid frames mixed in; every valid frame is still delivered."""
    # Verifies: IF-003, IF-004
    from bend_stand.io.framing import MAX_LEN, OVERHEAD, FrameDecoder, encode_frame

    rnd = random.Random(20261008)
    dec = FrameDecoder()
    sent = got = 0
    chunk_max = 4096
    t = 0
    for _ in range(600):
        parts = []
        for _ in range(rnd.randrange(1, 40)):
            k = rnd.random()
            if k < 0.3:
                parts.append(bytes([0xA5, 0x5A, rnd.randrange(256), rnd.randrange(256)])
                             + rnd.randrange(150, 400).to_bytes(2, "little"))
            elif k < 0.5:
                n = rnd.randrange(0, MAX_LEN + 1)
                parts.append(encode_frame(0xC0, rnd.randrange(256), bytes(rnd.randrange(256) for _ in range(n)))[
                    : rnd.randrange(1, OVERHEAD + n)])
            elif k < 0.7:
                parts.append(encode_frame(0xC1, rnd.randrange(256), bytes(16)))
                sent += 1
            else:
                parts.append(bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 64))))
        blob = b"".join(parts)
        for i in range(0, len(blob), chunk_max):
            t += 1_000_000
            got += sum(1 for f in dec.feed(blob[i:i + chunk_max], t, check_timeout=False) if f.type == 0xC1)
            assert dec.buffered <= chunk_max + OVERHEAD + MAX_LEN
        got += sum(1 for f in dec.poll(t + 50 * MS) if f.type == 0xC1)
    assert dec.max_buffered <= chunk_max + OVERHEAD + MAX_LEN
    assert got >= sent * 0.95, (got, sent)          # a few valid frames may be eaten by a preceding false header


@pytest.mark.req("SW-ACQ-002")
def test_swr_evidence_recording_folder_names_are_sanitised(tmp_path):
    """Evidence (no finding): specimen / number marks cannot escape the recordings root (path separators, ``..``,
    drive letters are replaced; the timestamp prefix keeps the name one path component)."""
    # Verifies: SW-ACQ-002
    from bend_stand.core.recorder import folder_name, unique_folder

    for spec, num in (("../../evil", "..\\x"), ("C:\\Windows", "/etc/passwd"), ("..", "."), ("a/b", "c:d")):
        name = folder_name("2026-10-08T12:00:00.000Z", spec, num)
        assert "/" not in name and "\\" not in name and ":" not in name, name
        f = unique_folder(tmp_path, name)
        assert f.parent == tmp_path, f


# ============================================================================================ §8 fix verification (v1.2)
# Independent tests of fixes that had only the implementer's tests, and reproducers of the new findings SWR-29 ….

def _blocking_channel(block_type: int):
    """CommandChannel over a fake transport whose write of the first ``block_type`` frame blocks until released."""
    from bend_stand.core.link import CommandChannel, FrameWriter

    order: list[int] = []
    entered, release = threading.Event(), threading.Event()

    class _Tr:
        def write(self, b: bytes) -> None:
            if b[2] == block_type and not entered.is_set():
                entered.set()
                release.wait(3.0)
            order.append(b[2])

    return CommandChannel(FrameWriter(_Tr()), seed=11), order, entered, release


@pytest.mark.req("SW-STOP-001", "IF-011", "IF-005")
@pytest.mark.defect("SWR-15")
def test_swr15_reserved_move_of_an_old_epoch_never_follows_a_stop():
    """SWR-15 fix (writes outside the channel lock, reservation order): a MOVE_ABS reserved while another thread is
    writing a lane frame, followed by a stop (``bump_epoch`` + priority STOP, as ``Device._priority_stop`` does), is
    never written — the STOP is on the wire, the MOVE future fails with ``CommandDropped``."""
    # Verifies: SW-STOP-001, IF-011, IF-005
    from bend_stand.core import protocol_gen as pg
    from bend_stand.core.link import CommandDropped
    from bend_stand.io import protocol as P

    ch, order, entered, release = _blocking_channel(int(pg.Cmd.GET_STATUS))
    a = threading.Thread(target=lambda: ch.submit(int(pg.Cmd.GET_STATUS)), daemon=True)
    a.start()
    assert entered.wait(2.0)
    mv = P.build_request(pg.Cmd.MOVE_ABS, target_um=10_000, v_um_s=1_000, a_um_s2=0)
    fut_mv = ch.submit(int(pg.Cmd.MOVE_ABS), mv, epoch=ch.motion_epoch)     # reserved; the writer is busy
    assert not fut_mv.done() and int(pg.Cmd.MOVE_ABS) not in order
    res: list[object] = []

    def stopper() -> None:
        ch.bump_epoch()
        res.append(ch.send_priority(int(pg.Cmd.STOP), bytes([0])))

    b = threading.Thread(target=stopper, daemon=True)
    b.start()
    time.sleep(0.05)
    release.set()
    a.join(3.0)
    b.join(3.0)
    assert int(pg.Cmd.STOP) in order and int(pg.Cmd.MOVE_ABS) not in order, order
    assert fut_mv.done() and isinstance(fut_mv.exception(), CommandDropped), fut_mv
    _fut, t_write, err = res[0]
    assert err is None and t_write is not None


@pytest.mark.req("SW-STOP-001", "IF-011")
@pytest.mark.defect("SWR-15")
def test_swr15_stop_waits_at_most_for_the_motion_frame_being_written():
    """SWR-15 fix: a MOVE_ABS already being written when the stop comes is on the wire **before** the STOP (the epoch
    bump waits for that one frame), and the STOP follows as soon as it is written."""
    # Verifies: SW-STOP-001, IF-011
    from bend_stand.core import protocol_gen as pg
    from bend_stand.io import protocol as P

    ch, order, entered, release = _blocking_channel(int(pg.Cmd.MOVE_ABS))
    mv = P.build_request(pg.Cmd.MOVE_ABS, target_um=10_000, v_um_s=1_000, a_um_s2=0)
    a = threading.Thread(target=lambda: ch.submit(int(pg.Cmd.MOVE_ABS), mv, epoch=ch.motion_epoch), daemon=True)
    a.start()
    assert entered.wait(2.0)
    b = threading.Thread(target=lambda: (ch.bump_epoch(), ch.send_priority(int(pg.Cmd.STOP), bytes([0]))),
                         daemon=True)
    b.start()
    time.sleep(0.05)
    assert int(pg.Cmd.STOP) not in order                 # the STOP waits for the frame on the wire
    t0 = time.monotonic()
    release.set()
    a.join(3.0)
    b.join(3.0)
    assert order == [int(pg.Cmd.MOVE_ABS), int(pg.Cmd.STOP)], order
    assert time.monotonic() - t0 < 0.5


@pytest.mark.req("SW-CFG-001", "SAF-SW-001")
@pytest.mark.defect("SWR-22")
def test_swr22_out_of_range_board_value_refuses_motion(lockstep, tmp_path, monkeypatch):
    """SWR-22 fix (system level, simulator): the board reports ``motion.steps_per_mm`` = 50 (dictionary range
    100 … 100 000) — a FW defect, emulated by letting the simulator encode its PARAM_ENTRY without the range check.
    Expected: the value is marked invalid and the motion gates refuse (PARAM_INVALID)."""
    # Verifies: SW-CFG-001, SAF-SW-001
    from bend_stand.core import params_gen as pgen
    from bend_stand.io import protocol as P

    unchecked = P.encode_param_entry_unchecked
    monkeypatch.setattr(P, "encode_param_entry",
                        lambda pid, value, ptype=None: unchecked(pid, int(pgen.BY_ID[pid].type) if ptype is None
                                                                 else ptype, value))
    scn = tmp_path / "bad.simscn.json"
    scn.write_text(json.dumps({"schema": "bird.bend.simscenario", "version": 1,
                               "params": {"motion.steps_per_mm": 50.0}}), encoding="utf-8")
    be = lockstep(endpoint=f"sim:{scn}")
    assert "PARAM_INVALID" in _codes(H.gate(be, "MOVE")), H.gate(be, "MOVE")
    assert "PARAM_INVALID" in _codes(H.gate(be, "JOG"))


@pytest.mark.req("SW-ACQ-003")
@pytest.mark.defect("SWR-23")
def test_swr23_samples_csv_neutralises_formula_marks(tmp_path):
    """SWR-23 fix: operator marks starting with ``= + - @`` are written to ``samples.csv`` as text (``'`` prefix)."""
    # Verifies: SW-ACQ-003
    import csv as _csv

    from bend_stand.core.model import SampleRow, TestMarks
    from bend_stand.core.recorder import append_sample

    m = TestMarks("=HYPERLINK(\"http://x\")", "+1", "-op", "@notes", (("k", "=1+1"),))
    p = tmp_path / "samples.csv"
    append_sample(p, SampleRow("2026-10-09T00:00:00Z", 1.0, 1.0, 80, 1.0, 0.1, 80, 1.0, 0.0, 1.0, 1.0, 80, m))
    with open(p, encoding="utf-8", newline="") as fh:
        rows = list(_csv.reader(fh))
    hdr, row = rows[0], rows[1]
    for col in ("specimen", "number", "operator", "notes", "custom"):
        v = row[hdr.index(col)]
        assert not v.startswith(("=", "+", "-", "@")), (col, v)


@pytest.mark.req("SW-REP-003")
@pytest.mark.defect("SWR-24")
def test_swr24_failed_calibration_cannot_be_applied_offline(tmp_path):
    """SWR-24 fix (Orchestrator decision): a ``--cal`` file is validated like an active calibration — a FAIL fit and a
    zero K are refused (``FileFormatError``), with or without a schema header."""
    # Verifies: SW-REP-003
    from bend_stand.core.errors import FileFormatError
    from bend_stand.core.recorder import COLUMNS
    from bend_stand.core.report import build_report

    (tmp_path / "meta.json").write_text(json.dumps({"schema": "bird.bend.recording", "schema_version": 1}),
                                        encoding="utf-8")
    (tmp_path / "data.csv").write_text("# x\n" + ",".join(COLUMNS) + "\n", encoding="utf-8")
    base = {"fit": {"k_n_per_count": 3e-4, "status": "FAIL"}, "afe": {"channel": "A", "gain": 128, "rate_sps": 80},
            "points": [{"raw_mean": 0.0, "force_n": 0.0}, {"raw_mean": 30000.0, "force_n": 9.8}]}
    zero_k = json.loads(json.dumps(base))
    zero_k["fit"].update(k_n_per_count=0.0, status="PASS")
    hdr = {"schema": "bird.bend.cal.load", "schema_version": 1, **base}
    for i, rec in enumerate((base, zero_k, hdr)):
        f = tmp_path / f"cal{i}.json"
        f.write_text(json.dumps(rec), encoding="utf-8")
        with pytest.raises(FileFormatError):
            build_report(tmp_path, cal=str(f), out_dir=tmp_path / f"out{i}")


@pytest.mark.req("SW-LIM-003")
@pytest.mark.defect("SWR-25")
def test_swr25_broken_session_file_is_never_overwritten():
    """SWR-25 fix (system level): a broken default session file is set aside (``.bad``); the next accepted change
    auto-saves a new file — the operator's content is still there."""
    # Verifies: SW-LIM-003
    from dataclasses import replace

    p = _data_dir() / "sessions" / "default.bbsession.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    broken = '{"schema": "bird.bend.session", "schema_version": 1, "limits": {"pull_trip_n": "100"}}'
    p.write_text(broken, encoding="utf-8")
    Backend, BackendSettings = H.backend_cls()
    be = Backend(BackendSettings(clock="lockstep", test_hooks=True))
    assert not be.session.set(replace(be.session.get(), sample_window_s=2.0))
    kept = [q for q in p.parent.iterdir() if q.name.startswith(p.name + ".bad")]
    assert kept and kept[0].read_text(encoding="utf-8") == broken, list(p.parent.iterdir())
    assert json.loads(p.read_text(encoding="utf-8"))["sample_window_s"] == 2.0


@pytest.mark.req("SAF-SW-005", "SW-PLT-001")
@pytest.mark.defect("SWR-14")
def test_swr14_incident_log_records_liveness_stop_halt_and_latches(lockstep, caplog):
    """SWR-14 fix: the incident log (``core.logfile``) records the stop paths — the liveness STOP of SWR-01 (source,
    reason), a HALT and its FW latch EVENT — at WARNING or ERROR where they are losses / faults."""
    # Verifies: SAF-SW-005, SW-PLT-001
    import logging

    from bend_stand.core.logfile import attach_incident_log

    be = lockstep()
    attach_incident_log(be.events)
    caplog.set_level(logging.INFO)
    H.m2_ready(be)
    H.move_to(be, 100.0, speed_mm_s=5.0)
    H.advance(be, 500)
    H.stall(be, "pipeline", 600)
    H.advance(be, 800)
    H.halt(be)
    H.advance(be, 300)
    text = "\n".join(f"{r.levelname} {r.name} {r.getMessage()}" for r in caplog.records)
    assert "liveness" in text and "STOP issued by liveness" in text, text[-3000:]
    assert "HALT issued by" in text and "FW EVENT HALT_SET" in text, text[-3000:]
    assert any(r.levelno >= logging.WARNING and "HALT_SET" in r.getMessage() for r in caplog.records)


@pytest.mark.req("SW-PLT-001")
@pytest.mark.defect("SWR-31")
def test_swr31_log_set_up_never_blocks_the_start(monkeypatch):
    """SWR-14 fix, residual: ``setup_logging`` handles ``OSError`` only; ``paths.app_data_dir`` raises
    ``RuntimeError`` when neither ``%APPDATA%`` nor a home directory resolve, and ``main()`` calls it before its
    ``try`` → the application does not start because of the log file. Expected: console-only logging, no raise."""
    # Verifies: SW-PLT-001
    from bend_stand.core import logfile

    def boom(_override=None):
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(logfile.paths, "app_data_dir", boom)
    try:
        r = logfile.setup_logging("WARNING")
        assert r.path is None
    finally:
        logfile.shutdown_logging()


@pytest.mark.req("SW-SEQ-002", "SW-REP-001")
@pytest.mark.defect("SWR-30")
def test_swr30_report_warns_about_a_truncated_run_log(tmp_path):
    """SWR-28 fix, residual: the run log of an endless loop is bounded (``sequence_runs[].truncated``), but the
    report built from it shows only the kept windows without saying that older ones were dropped. Expected: a
    report warning naming the truncation."""
    # Verifies: SW-SEQ-002, SW-REP-001
    from bend_stand.core.recorder import COLUMNS
    from bend_stand.core.report import build_report

    (tmp_path / "meta.json").write_text(json.dumps({
        "schema": "bird.bend.recording", "schema_version": 1,
        "sequence_runs": [{"sequence": {"name": "endless"}, "state": "STOPPED", "windows": [], "results": [],
                           "truncated": {"windows": 5000, "results": 5000, "events": 20000}}]}), encoding="utf-8")
    (tmp_path / "data.csv").write_text("# x\n" + ",".join(COLUMNS) + "\n", encoding="utf-8")
    doc = json.loads(Path(build_report(tmp_path).json).read_text(encoding="utf-8"))
    assert any("trunc" in w.lower() or "dropped" in w.lower() for w in doc["warnings"]), doc["warnings"]


@pytest.mark.req("SW-LIM-004", "SAF-SW-001", "SW-LIM-003")
@pytest.mark.defect("SWR-29")
def test_swr29_leaving_no_specimen_mode_does_not_keep_both_limits_off(lockstep):
    """D-53 a (SWR-03 fix, residual): 'switching both SW load limits off outside the no-specimen mode is refused'.
    Inside the mode both may be switched off; leaving the mode (operator, or link down) keeps them off — with a
    valid calibration + tare the axis then moves without any PC load limit, without the mode and its banner, and
    the auto-saved session carries both-off. Expected: leaving the mode restores the limits (or is refused)."""
    # Verifies: SW-LIM-004, SAF-SW-001, SW-LIM-003
    be = lockstep()
    H.calibrate_and_tare(be)
    g = be.limits.set_no_specimen_mode(True, confirmed=True)
    assert g.ok, g
    assert not H.set_limits(be, pull_enabled=False, push_enabled=False)
    be.limits.set_no_specimen_mode(False)
    lim = be.limits.get()
    assert H.safety(be).no_specimen_mode or lim.pull_enabled or lim.push_enabled, lim


@pytest.mark.req("SW-ACQ-002", "SW-ACQ-004")
@pytest.mark.defect("SWR-05")
@pytest.mark.rt
def test_swr05_threaded_stop_start_cycles_keep_every_row_in_one_recording(tmp_path):
    """SWR-05 / SWR-33 / SWD-P3-01 (independent of B's ``test_rec_boundary``): a producer thread delivers rows with
    increasing receive stamps without pause while the main thread runs 25 stop / start cycles on the real clock.
    Oracle: no row appears in two recordings, rows of each file are strictly increasing, every recording starts after
    the previous one's last row, no row received (host stamp) before the previous ``stop()`` returned is in a later
    recording (the e600169 leak), and ``integrity.rows_written`` equals the D rows in the file."""
    # Verifies: SW-ACQ-002, SW-ACQ-004
    from bend_stand.core.clock import MONOTONIC
    from bend_stand.core.pipeline import DataRow
    from bend_stand.core.recorder import Recorder

    rec = Recorder(MONOTONIC)
    rec.free_space_probe = lambda _p: 10 ** 12
    stop_ev = threading.Event()
    seq = [0]

    def producer() -> None:
        while not stop_ev.is_set():
            i = seq[0]
            seq[0] += 1
            rec.on_row(DataRow(MONOTONIC.monotonic_ns(), i, i, i * 1e-6, i & 0xFFFF, 0, 4, 0, 100, 0, 0, 0, 0))
            if i % 50 == 0:
                time.sleep(0.0005)

    folders = []
    stopped_at = [0]
    th = threading.Thread(target=producer, daemon=True)
    th.start()
    try:
        for k in range(25):
            folders.append(rec.start(tmp_path / f"r{k:02d}", {}))
            time.sleep(0.02)
            rec.stop()
            stopped_at.append(MONOTONIC.monotonic_ns())
    finally:
        stop_ev.set()
        th.join(2.0)
    seen: set[int] = set()
    prev_last = -1
    for k, f in enumerate(folders):
        lines = [ln.split(",") for ln in (f / "data.csv").read_text().splitlines() if ln.startswith("D,")]
        rows = [int(c[2]) for c in lines]
        assert all(int(c[4]) > stopped_at[k] for c in lines), (f, "row of an earlier recording")
        assert rows == sorted(set(rows)), f
        assert not seen & set(rows), f
        if rows:
            assert rows[0] > prev_last, (f, rows[0], prev_last)
            prev_last = rows[-1]
        seen |= set(rows)
        meta = json.loads((f / "meta.json").read_text(encoding="utf-8"))
        assert meta["integrity"]["rows_written"] == len(rows), (f, meta["integrity"], len(rows))


@pytest.mark.req("SAF-SW-001", "SAF-SW-003")
@pytest.mark.defect("SWR-37")
def test_swr37_pipeline_stall_not_hidden_by_repeated_supervisor_stalls(lockstep):
    """SWR-01 follow-up (OBS-P3-05 filter): the Pipeline-liveness condition is reset by every Supervisor gap > 100 ms
    and needs 30 ms of continuous Supervisor operation. Stimulus: the Pipeline is stalled for 2.5 s during a move while
    the Supervisor is silent 120 ms out of every 140 ms (the pattern of repeated process stalls). The Reader keeps
    receiving DATA, so the DATA-loss STOP does not fire, and the Supervisor's short windows keep the FW link watchdog
    fed. Expected: a STOP once the unevaluated DATA is clearly older than any process stall (review proposal: an
    absolute ceiling, e.g. oldest unevaluated frame > 1 s, independent of the Supervisor gap)."""
    # Verifies: SAF-SW-001, SAF-SW-003
    be = lockstep()
    H.m2_ready(be)
    H.move_to(be, 100.0, speed_mm_s=5.0)
    H.advance(be, 500)
    assert H.motion(be).moving
    m0 = H.wire_mark(be)
    H.stall(be, "pipeline", 2500)
    for _ in range(17):
        H.stall(be, "supervisor", 120)
        H.advance(be, 120)
        H.advance(be, 20)
    assert H.tx(be, "STOP", since=m0), sorted({w.name for w in H.tx(be, since=m0)})

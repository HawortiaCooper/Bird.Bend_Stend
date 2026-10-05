"""M3 recording, take-sample, marks / presets and session files.

Verifies: SW-ACQ-002, SW-ACQ-003, SW-ACQ-004, SW-META-001, SW-META-002, SW-LIM-003
"""
from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from bbs_support import lockstep_backend
from bend_stand.core import session as session_mod
from bend_stand.core.clock import FakeClock
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import Bend3pGeometry, SessionSettings, TestMarks
from bend_stand.core.pipeline import DataRow
from bend_stand.core.recorder import COLUMNS, QUEUE_ROWS, Recorder
from bend_stand.core.scaling import DERIVED_KEYS


def _read(folder: Path):
    lines = (folder / "data.csv").read_text(encoding="utf-8").splitlines()
    hdr = next(i for i, ln in enumerate(lines) if not ln.startswith("# "))
    return lines[:hdr], list(csv.DictReader(lines[hdr:])), json.loads((folder / "meta.json").read_text("utf-8"))


@pytest.mark.req("SW-ACQ-002", "SW-META-001", "SW-META-002", "SW-LIM-003", "SW-ACQ-003")
def test_recording_derived_columns_event_rows_snapshot_and_samples(tmp_path) -> None:
    be = lockstep_backend(recordings_root=str(tmp_path))
    try:
        h = be.test_hooks
        be.marks.set(TestMarks("beam A", "3", "op", "n", (("lot", "L7"),)))
        assert be.record_start().ok
        h.advance(500)
        be.marks.set(TestMarks("beam A", "3", "op", "n", (("lot", "L8"), ("temp", "21"))))
        assert be.limits.set_no_specimen_mode(True, confirmed=True).ok
        assert be.take_sample(0.5).ok
        assert not be.take_sample(0.5).ok                                  # one sample capture at a time
        h.advance(800)
        assert be.tare(window_s=2.0).ok
        h.advance(2500)
        assert be.record_stop().ok
        folder = next(tmp_path.iterdir())
        head, rows, meta = _read(folder)
        assert list(rows[0].keys()) == list(COLUMNS)
        d = [r for r in rows if r["row_type"] == "D"]
        assert d and all(r["F_N"] == "nan" for r in d[:5]) and float(d[-1]["noise_counts"]) > 0
        ev = [r["event"].split(":")[0] for r in rows if r["row_type"] == "E"]
        assert {"MARK_EDIT", "NO_SPECIMEN_ON", "SAMPLE", "TARE"} <= set(ev)
        assert [int(r["t_host"]) for r in rows] == sorted(int(r["t_host"]) for r in rows)
        assert any(ln.startswith("# limits ") for ln in head) and any(ln.startswith("# calibration ") for ln in head)
        assert meta["marks_at_start"]["custom"] == [{"key": "lot", "value": "L7"}]
        assert meta["marks_final"]["custom"][1] == {"key": "temp", "value": "21"}
        assert {e["key"] for e in meta["mark_edits"]} == {"custom.lot", "custom.temp"}
        snap = meta["snapshot"]
        assert snap["limits"]["fw_level_n"] == pytest.approx(2157.46) and snap["session"]["schema"] == \
            "bird.bend.session"
        assert meta["integrity"]["complete"] is True
        samples = list(csv.DictReader((folder / "samples.csv").read_text(encoding="utf-8").splitlines()))
        assert len(samples) == 1 and int(samples[0]["n"]) == pytest.approx(40, abs=2)
        assert samples[0]["specimen"] == "beam A" and float(samples[0]["raw_std"]) > 0
        tk = be.events.history("sample.taken")[-1].payload
        assert tk.raw_n == int(samples[0]["raw_n"]) and tk.file.endswith("samples.csv")
        # without a recording: the daily samples file under the root
        assert be.take_sample(0.2).ok
        h.advance(400)
        assert list((tmp_path / "samples").glob("samples_*.csv"))
        assert not be.take_sample(20.0).ok
    finally:
        be.shutdown()


@pytest.mark.req("SW-ACQ-004")
def test_low_free_space_refuses_start_and_fails_a_running_recording(tmp_path) -> None:
    be = lockstep_backend(recordings_root=str(tmp_path))
    try:
        h = be.test_hooks
        h.set_free_space(100 * 1024 * 1024)
        g = be.record_start()
        assert not g.ok and "free disk space" in g.text()
        h.set_free_space(None)
        assert be.record_start().ok
        h.set_free_space(10)
        h.advance(10_500)
        st = be.status()
        assert st.recording.state == "FAILED" and "free disk space" in st.recording.failure
        assert be.events.history("rec.failure") and st.indicators.recording_failed.state == "ON"
        be.record_stop()
    finally:
        be.shutdown()


def _row(i: int) -> DataRow:
    return DataRow(1000 + i, i, i, i / 80.0, i & 0xFFFF, 0, 0, 0, 50_000, 0, 0, 0, 0,
                   tuple(float(i) for _ in DERIVED_KEYS), 0)


@pytest.mark.req("SW-ACQ-004")
def test_queue_overflow_is_counted_and_marked_with_rec_gap(tmp_path) -> None:
    rec = Recorder(FakeClock())
    warn = []
    rec.on_warning = warn.append
    rec.free_space_probe = lambda _p: None
    rec.start(tmp_path, {"sw_version": "t"})
    for i in range(QUEUE_ROWS + 10):                                       # writer not running (lock-step)
        rec.on_row(_row(i))
    rec.event_row("NOTE", "a,b\nc")
    assert rec.rows_lost == 11 and warn
    rec.step()
    rec.on_row(_row(QUEUE_ROWS + 20))
    rec.step()
    rec.stop()
    lines = (rec.folder / "data.csv").read_text(encoding="utf-8").splitlines()
    gap = [ln for ln in lines if "REC_GAP" in ln]
    assert len(gap) == 1 and "11 rows lost" in gap[0]
    meta = json.loads((rec.folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["integrity"]["complete"] is False and meta["integrity"]["rows_lost"] == 11
    assert rec.stop() is None


@pytest.mark.req("SW-META-001", "SW-META-002")
def test_marks_rules_and_presets(tmp_path) -> None:
    be = lockstep_backend(connect=False)
    try:
        with pytest.raises(ValueError):
            be.marks.set(TestMarks(custom=(("a", "1"), ("A", "2"))))
        with pytest.raises(ValueError):
            be.marks.set(TestMarks(custom=((" ", "1"),)))
        m = TestMarks("s", "1", "op", "x", (("k", "v"),))
        be.marks.set(m)
        be.marks.save_preset("default")                                     # bare name → presets folder
        p = be.marks.list_presets()
        assert len(p) == 1 and p[0].endswith("default.bbmarks.json")
        be.marks.set(TestMarks())
        assert be.marks.load_preset(p[0]) == m and be.marks.get() == m
        bad = tmp_path / "bad.bbmarks.json"
        bad.write_text(json.dumps({"schema": "bird.bend.marks", "schema_version": 1,
                                   "custom": [{"key": "a"}, {"key": "a"}]}), encoding="utf-8")
        with pytest.raises(FileFormatError):
            be.marks.load_preset(str(bad))
    finally:
        be.shutdown()


@pytest.mark.req("SW-LIM-003")
def test_session_file_round_trip_validation_and_start_loading(tmp_path) -> None:
    s = replace(SessionSettings(), display_unit="kgf", pull_dir=-1, k_est_n_mm=40.0,
                bend3p=Bend3pGeometry(100.0, 20.0, 5.0))
    s = replace(s, limits=replace(s.limits, travel_max_mm=120.0, travel_max_enabled=True, pull_trip_n=800.0))
    path = tmp_path / "a.bbsession.json"
    session_mod.save(path, s)
    back, issues = session_mod.load(path)
    assert back == s and not issues
    d = json.loads(path.read_text(encoding="utf-8"))
    assert "tare" not in json.dumps(d).lower().replace("tare_window_s", "") and "no_specimen" not in d
    d["future"] = 1
    d["limits"]["x"] = 2
    path.write_text(json.dumps(d), encoding="utf-8")
    assert {i.code for i in session_mod.load(path)[1]} == {"UNKNOWN_KEY"}
    d["schema_version"] = 99
    path.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(FileFormatError):
        session_mod.load(path)
    for bad in ({"pull_dir": 2}, {"tare_window_s": 1.0}, {"display_unit": "lb"}, {"bend3p": [1]},
                {"limits": 5}, {"bend3p": {"span_mm": 1}}):
        dd = dict(json.loads(json.dumps(session_mod.to_dict(SessionSettings()))), **bad)
        path.write_text(json.dumps(dd), encoding="utf-8")
        with pytest.raises(FileFormatError):
            session_mod.load(path)
    assert {i.key for i in session_mod.validate(replace(SessionSettings(), manual_accel_mm_s2=-1.0,
                                                        expected_spm=50.0))} == {"manual_accel_mm_s2",
                                                                                 "expected_spm"}
    # applied at start
    session_mod.save(path, s)
    be = lockstep_backend(connect=False, session_path=str(path))
    try:
        assert be.session.get() == s and be.limits.get().pull_trip_n == 800.0
        out = tmp_path / "b.bbsession.json"
        be.session.save(str(out))
        assert be.session.load(str(out)) == s
    finally:
        be.shutdown()
    path.write_text("{broken", encoding="utf-8")
    be = lockstep_backend(connect=False, session_path=str(path))
    try:
        assert be.session.get() == SessionSettings() and be.session.load_issues
    finally:
        be.shutdown()


@pytest.mark.req("SAF-SW-003")
def test_link_lost_logs_tick_gaps_and_gc_diagnostics(tmp_path) -> None:
    """MC3-4 (OBS-M3-R1): every LINK LOST is logged with the longest reader / pipeline / supervisor tick gap, the
    receive age and the GC pauses; the same text goes into a running recording as a LINK_LOST row."""
    import gc  # noqa: PLC0415

    from bend_stand.core.timing import GcWatch  # noqa: PLC0415

    be = lockstep_backend(recordings_root=str(tmp_path))
    try:
        h = be.test_hooks
        be._gc_watch = GcWatch()                                     # noqa: SLF001 (real-clock only otherwise)
        be._gc_watch.install()                                       # noqa: SLF001
        gc.collect()
        assert be.record_start().ok
        h.stall_thread("pipeline", 1500)
        be.sim.act("inject", fault="hang", duration_ms=1500)
        assert h.run_until(lambda: be.status().link.state.value == "LOST", 3000)
        rec = [e for e in be.events.history("log") if e.payload.get("text") == "LINK LOST"][-1]
        diag = rec.payload["diagnostics"]
        assert diag["tick_gap_ms"]["pipeline"] >= 900 and "reader" in diag["tick_gap_ms"]
        assert diag["rx_age_ms"] >= 900 and "gc_count" in diag and diag["gc_count"] >= 1
        h.advance(3000)
        be.record_stop()
        text = (next(tmp_path.iterdir()) / "data.csv").read_text(encoding="utf-8")
        assert "LINK_LOST:" in text and "tick_gap_ms=" in text
        be._gc_watch.uninstall()                                     # noqa: SLF001
        assert GcWatch().summary(10**9) == {"gc_count": 0, "gc_total_ms": 0.0, "gc_max_ms": 0.0}
    finally:
        be.shutdown()

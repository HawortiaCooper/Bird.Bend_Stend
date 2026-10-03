"""Level C — stream toggle, recorder skeleton, endurance (Validator F, M1).

Ground truth: the simulator's sent-frame log (``query sent``: every DATA frame the board produced, incl. the
ones it dropped) and F's FBoard sent list; the PC wire log re-parsed with ``ref_codec``.

TC-SW-ACQ-001-01 (C part), recorder M1 skeleton (SW-ACQ-002/-004 basis: every frame exactly once, no silent
loss), TC-NFR-004-02 (1 h of device time, lock-step), TC-NFR-001-02 (informative smoke, see the GUI module).

Verifies: SW-ACQ-001, SW-ACQ-002, SW-ACQ-004, NFR-004, IF-007
"""
from __future__ import annotations

import csv
import errno
import json
import time
from pathlib import Path

import pytest

import harness as H
from oracle.fboard import FBoard


def _read_rec(folder: Path):
    lines = (folder / "data.csv").read_text(encoding="utf-8").splitlines()
    hdr = next(i for i, ln in enumerate(lines) if not ln.startswith("# "))
    rows = list(csv.DictReader(lines[hdr:]))
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    return lines[:hdr], rows, meta


def _rec_folder(be) -> Path:
    return Path(H.status(be).recording.folder)


@pytest.mark.req("SW-ACQ-001")
def test_tc_sw_acq_001_01_stream_toggle_and_gates(vbe):
    """Stream stop / start through the API: state follows the board (STREAM_ON), DATA stops / resumes, frame_seq
    continues (not reset by STREAM_STOP/START, ICD §2.2) → no loss counted; gates mirror the state."""
    # Verifies: SW-ACQ-001
    assert H.gate(vbe, "STREAM_STOP").ok and not H.gate(vbe, "STREAM_START").ok
    H.result(vbe, H.stream(vbe, False))
    H.advance(vbe, 300)
    m0 = H.wire_mark(vbe)
    H.advance(vbe, 1000)
    assert not H.rx(vbe, "DATA", since=m0)
    assert not H.status(vbe).stream.on and H.gate(vbe, "STREAM_START").ok and not H.gate(vbe, "STREAM_STOP").ok
    H.result(vbe, H.stream(vbe, True))
    H.advance(vbe, 500)
    assert H.rx(vbe, "DATA", since=m0) and H.status(vbe).stream.on
    s = H.stats(vbe)
    assert s.frames_lost_fw == s.frames_lost_link == s.seq_anomalies == 0


@pytest.mark.req("SW-ACQ-001", "SAF-SW-003")
def test_tc_sw_acq_001_01_stream_stop_refused_while_moving(vbe):
    """STREAM_STOP is refused while the axis moves (the PC limits need the data): gate REFUSE + the async call
    fails, nothing on the wire. (Motion is produced on the forced path — the M1 API cannot move.)"""
    # Verifies: SW-ACQ-001, SAF-SW-003
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 200_000))
    assert H.run_until(vbe, lambda: H.status(vbe).motion.moving, 500)
    assert not H.gate(vbe, "STREAM_STOP").ok
    m0 = H.wire_mark(vbe)
    with pytest.raises(Exception):
        H.result(vbe, H.stream(vbe, False))
    assert not H.tx(vbe, "STREAM_STOP", since=m0)
    H.stop(vbe)
    H.advance(vbe, 300)


@pytest.mark.req("SW-ACQ-002", "SW-ACQ-004", "IF-007")
def test_recorder_every_frame_exactly_once_with_fw_drops_and_events(vbe):
    """Recording 30 s with an injected FW TX congestion and a PAUSE: data.csv D rows == the simulator's sent
    DATA frames that were not dropped, each exactly once and in order (frame_seq, t_us); seq_lost on the row
    after the gap = frames dropped; E rows for the EVENTs in arrival order; meta.json integrity complete."""
    # Verifies: SW-ACQ-002, SW-ACQ-004, IF-007
    H.set_marks(vbe, "spec A/1", "7")
    n0 = len(H.sim_sent(vbe))
    assert H.record_start(vbe).ok
    H.advance(vbe, 5000, 2)
    H.act(vbe, "inject", fault="tx_congestion", duration_ms=150)
    H.advance(vbe, 5000, 2)
    H.pause(vbe)
    H.advance(vbe, 2000, 2)
    H.resume(vbe)
    H.advance(vbe, 18_000, 2)
    assert H.record_stop(vbe).ok
    folder = _rec_folder(vbe)
    head, rows, meta = _read_rec(folder)
    assert head[0] == "# bird.bend.data/1" and folder.name.endswith("_spec-A-1_7")
    d_rows = [r for r in rows if r["row_type"] == "D"]
    sent = [f for f in H.sim_sent(vbe)[n0:] if f["type"] == 0xC0]
    first = int(d_rows[0]["frame_seq"])
    last = int(d_rows[-1]["frame_seq"])
    truth = [(f["frame_seq"], f["t_us"]) for f in sent if not f["dropped"] and first <= f["frame_seq"] <= last]
    got = [(int(r["frame_seq"]), int(r["t_us"])) for r in d_rows]
    assert got == truth
    dropped = [f["frame_seq"] for f in sent if f["dropped"] and first <= f["frame_seq"] <= last]
    assert len(dropped) >= 5
    assert sum(int(r["seq_lost"]) for r in d_rows) == len(dropped)
    ev = [r["event"].split(":")[0] for r in rows if r["row_type"] == "E"]
    assert "PAUSED" in ev and "PAUSE_CLEARED" in ev and ev.index("PAUSED") < ev.index("PAUSE_CLEARED")
    host = [int(r["t_host"]) for r in rows]
    assert host == sorted(host)                                        # arrival order
    integ = meta["integrity"]
    assert integ["complete"] is True and integ["rows_lost"] == 0 and integ["rows_written"] == len(rows)
    assert int(meta["param_dict_hash"], 16) == H.status(vbe).link.info.param_dict_hash


@pytest.mark.req("SW-ACQ-004", "SW-ACQ-002")
def test_recorder_disk_full_never_silent(vbe):
    """FI-25 (M1 skeleton): ENOSPC from the writer → state FAILED with the reason, rows counted as lost (never
    silent): rows_written + rows_lost == rows delivered; meta.json complete = false with the failure."""
    # Verifies: SW-ACQ-004, SW-ACQ-002
    assert H.record_start(vbe).ok
    H.advance(vbe, 2000, 2)
    d0 = H.stats(vbe).data_frames
    H.fail_recorder(vbe, OSError(errno.ENOSPC, "No space left on device"))
    H.advance(vbe, 3000, 2)
    st = H.status(vbe).recording
    assert st.state == "FAILED" and "No space" in (st.failure or "")
    assert H.indicator(vbe, "recording_failed").state == "ON"
    d1 = H.stats(vbe).data_frames
    assert st.rows_lost >= d1 - d0 - 2
    assert H.record_stop(vbe).ok
    _head, rows, meta = _read_rec(_rec_folder(vbe))
    integ = meta["integrity"]
    assert integ["complete"] is False and integ["failures"] and integ["rows_written"] == len(rows)
    assert integ["rows_written"] + integ["rows_lost"] >= H.stats(vbe).data_frames - 5


@pytest.mark.req("SW-ACQ-002", "IF-007")
def test_recorder_duplicates_and_link_gaps_fboard(pdict, tmp_path):
    """FI-03 / FI-01 on the F-board: a duplicated DATA frame is recorded once; a link-attributed gap shows in
    seq_lost; the recording equals the F-board's distinct sent frames that arrived during the recording."""
    # Verifies: SW-ACQ-002, IF-007
    fb = FBoard(pdict, skip_every=40, dup_every=25)
    be = H.realtime_backend(recordings_root=str(tmp_path / "rec"))
    try:
        H.connect(be, fb.endpoint).result(10)
        assert H.wait_rt(lambda: H.status(be).stream.on, 5)
        assert H.record_start(be).ok
        time.sleep(4.0)
        assert H.record_stop(be).ok
        _h, rows, meta = _read_rec(_rec_folder(be))
        seqs = [int(r["frame_seq"]) for r in rows if r["row_type"] == "D"]
        assert len(seqs) == len(set(seqs)) and seqs == sorted(seqs)
        i0, i1 = fb.sent_seqs.index(seqs[0]), fb.sent_seqs.index(seqs[-1])
        assert seqs == fb.sent_seqs[i0:i1 + 1]
        lost = sum(int(r["seq_lost"]) for r in rows if r["row_type"] == "D")
        assert lost == sum(1 for a, b in zip(seqs, seqs[1:]) if b - a == 2)
        assert meta["integrity"]["rows_lost"] == 0
    finally:
        be.shutdown()
        fb.close()


@pytest.mark.req("NFR-004", "SW-ACQ-002", "IF-007")
def test_tc_nfr_004_02_one_hour_device_time_lockstep(lockstep, tmp_path):
    """TC-NFR-004-02: 1 h of device time (≈ 289 440 frames, frame_seq wraps 4×), recording on: 0 SW-attributable
    losses (async_overflow 0, rows_lost 0), 0 link / FW losses, no seq anomaly; the recording holds every sent
    frame exactly once; process memory growth after the first 15 min ≤ 50 MB (informative on DEV)."""
    # Verifies: NFR-004, SW-ACQ-002, IF-007
    be = lockstep(wire_log=False, recordings_root=str(tmp_path / "rec"))
    n0 = len(H.sim_sent(be))
    assert H.record_start(be).ok
    H.advance(be, 15 * 60_000, 5)
    rss15 = H.rss_bytes()
    H.advance(be, 45 * 60_000, 5)
    rss60 = H.rss_bytes()
    assert H.record_stop(be).ok
    s = H.stats(be)
    assert s.async_overflow == 0 and s.frames_lost_fw == 0 and s.frames_lost_link == 0 and s.seq_anomalies == 0
    _h, rows, meta = _read_rec(_rec_folder(be))
    assert meta["integrity"]["rows_lost"] == 0 and meta["integrity"]["complete"] is True
    d = [(int(r["frame_seq"]), int(r["t_us"])) for r in rows if r["row_type"] == "D"]
    sent = [(f["frame_seq"], f["t_us"]) for f in H.sim_sent(be)[n0:] if f["type"] == 0xC0 and not f["dropped"]]
    j = sent.index(d[-1])                       # the board's sent log is a 200 000-entry ring: compare its tail
    k = min(len(d), j + 1)
    assert k >= 150_000 and d[-k:] == sent[j - k + 1:j + 1]
    assert len(d) >= 289_000 and len(set(d)) == len(d)
    if rss15 and rss60:
        assert (rss60 - rss15) / 2**20 <= 50.0


@pytest.mark.req("SW-ACQ-002")
@pytest.mark.defect("SWD-M1-09")
def test_recording_restart_within_one_second(vbe):
    """Stop and immediately start a new recording (same marks): the second recording starts in its own folder."""
    # Verifies: SW-ACQ-002
    assert H.record_start(vbe).ok
    H.advance(vbe, 300)
    assert H.record_stop(vbe).ok
    g = H.record_start(vbe)
    assert g.ok, g
    H.advance(vbe, 300)
    assert H.record_stop(vbe).ok

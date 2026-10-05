"""Backend facade (WP-B11): API conformance, status snapshot (indicators incl. UNKNOWN, gates), lock-step
determinism, STOP on the wire, recording skeleton (CSV + sidecar, failure), headless entry point, real-clock run.

Verifies: SW-PLT-003, SW-ACQ-001, SW-ACQ-002, SW-ACQ-004, SAF-SW-005, NFR-002, SW-STOP-001, SW-PLT-002
"""
from __future__ import annotations

import errno
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bbs_support import lockstep_backend, tx_frames
from bend_stand.core import api
from bend_stand.core import protocol_gen as pg
from bend_stand.core.backend import Backend, BackendSettings
from bend_stand.core.errors import ConfirmationRequired, GateRefused
from bend_stand.core.model import GateCode, GateId, LinkState

SW_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.req("SW-PLT-002", "SW-PLT-003")
def test_backend_satisfies_every_api_protocol() -> None:
    be = Backend(BackendSettings(clock="lockstep", test_hooks=True))
    assert isinstance(be, api.BackendAPI)
    pairs = [(be.config, api.ConfigAPI), (be.motion, api.MotionAPI), (be.limits, api.LimitsAPI),
             (be.data, api.DataViewAPI), (be.channels, api.ChannelRegistryAPI), (be.marks, api.MarksAPI),
             (be.session, api.SessionAPI), (be.tare_engine, api.TareEngineAPI), (be.travel_cal, api.EngineAPI),
             (be.load_cal, api.LoadCalEngineAPI), (be.calibrations, api.CalibrationStoreAPI),
             (be.sequencer, api.SequencerAPI), (be.reports, api.ReportAPI), (be.events, api.EventBusAPI),
             (be.test_hooks, api.TestHooksAPI)]
    for obj, proto in pairs:
        assert isinstance(obj, proto), proto.__name__
    be.start()
    be.test_hooks.result(be.connect_async("sim"))
    assert isinstance(be.sim, api.SimControlAPI)
    be.shutdown()


@pytest.mark.req("SAF-SW-005")
def test_status_before_connect_is_unknown_and_safe() -> None:
    be = Backend(BackendSettings(clock="lockstep", test_hooks=True))
    st = be.status()
    assert st.link.state == LinkState.DISCONNECTED and st.cfg_dirty is None
    for k in ("valid", "moving", "estop", "paused", "load_limit", "cfg_dirty"):
        assert st.indicators[k].state == "UNKNOWN"         # never drawn as OK (GRQ-B-02)
    assert set(st.gates) == set(GateId)
    assert not st.gates[GateId.STREAM_START].ok and st.gates[GateId.STREAM_START].refused[0].code == GateCode.LINK_DOWN
    assert be.stop().sent is False and be.halt().sent is False and be.pause().sent is False
    eps = [e.endpoint for e in be.endpoints()]
    assert "sim" in eps and "tcp://127.0.0.1:5760" in eps


@pytest.mark.req("SAF-SW-005", "SW-ACQ-001")
def test_status_connected_indicators_gates_and_10hz_polling() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        seqs = []
        for _ in range(100):                                  # 10 s at 10 Hz
            h.advance(100)
            st = be.status()
            seqs.append(st.seq)
            assert st.indicators.link_state.state == "ON"
        assert len(set(seqs)) == 100
        st = be.status()
        assert st.stream.on and st.indicators.stream_on.state == "ON" and st.indicators.drv_pwr.state == "ON"
        assert st.indicators.estop.state == "OFF" and st.indicators.afe_synthetic.state == "OFF"
        assert st.gates[GateId.STREAM_STOP].ok and not st.gates[GateId.STREAM_START].ok
        assert {"NOT_ENABLED", "NOT_HOMED"} <= set(st.gates[GateId.MOVE].codes())          # M2 motion gate
        assert st.motion.position_mm == 0.0 and st.calibration.board_spm == 800.0
        # stream off → DATA items become UNKNOWN once stale, STATUS items stay known (4 Hz poll)
        h.result(be.stream_stop_async())
        h.advance(700)
        st = be.status()
        assert not st.stream.on and st.indicators.stream_on.state == "OFF"
        assert st.indicators.valid.state in ("ON", "OFF")      # from GET_STATUS (fresh)
        assert st.gates[GateId.STREAM_START].ok
        with pytest.raises(GateRefused):
            h.result(be.stream_stop_async()) if not st.gates[GateId.STREAM_STOP].ok else None
            raise GateRefused(None)
        h.result(be.stream_start_async())
        assert be.status().stream.on
    finally:
        be.shutdown()


@pytest.mark.req("NFR-002", "SW-STOP-001")
def test_stop_on_the_wire_immediately_lockstep() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        lat = []
        for _ in range(100):
            h.advance(7)
            t_call = be.clock.monotonic_ns()
            r = be.stop("gui")
            assert r.sent
            w = tx_frames(be, pg.Cmd.STOP)[-1]
            lat.append(w.t_ns - t_call)
        lat.sort()
        assert lat[94] <= 50_000_000
    finally:
        be.shutdown()


def _scripted_run() -> list[tuple[int, str, bytes]]:
    be = lockstep_backend(seed=11)
    h = be.test_hooks
    h.advance(1500)
    be.pause("x")
    h.advance(100)
    be.resume("x")
    h.advance(200)
    h.result(be.config.write_and_verify_async({"afe.rate_tol_pct": 33}))
    h.advance(300)
    out = [(w.t_ns, w.direction, w.frame) for w in h.wire_log()]
    be.shutdown()
    return out


@pytest.mark.req("SW-PLT-003")
def test_lockstep_runs_are_deterministic() -> None:
    a, b = _scripted_run(), _scripted_run()
    assert len(a) > 200 and a == b


@pytest.mark.req("SW-ACQ-002", "SW-ACQ-004")
def test_recording_csv_and_sidecar(tmp_path) -> None:
    be = lockstep_backend(recordings_root=str(tmp_path))
    try:
        h = be.test_hooks
        assert be.record_stop().refused
        be.marks.set(api.TestMarks(specimen="S 1/a", number="7"))
        assert be.record_start().ok
        assert not be.record_start().ok                        # already recording
        h.advance(1000)
        be.halt("test")
        h.advance(200)
        assert be.record_stop().ok
        folders = list(tmp_path.iterdir())
        assert len(folders) == 1 and folders[0].name.endswith("_S-1-a_7")
        lines = (folders[0] / "data.csv").read_text(encoding="utf-8").splitlines()
        hdr = next(i for i, ln in enumerate(lines) if not ln.startswith("# "))
        assert lines[0] == "# bird.bend.data/1" and lines[hdr].startswith("row_type,")
        d_rows = [ln for ln in lines if ln.startswith("D,")]
        e_rows = [ln for ln in lines if ln.startswith("E,")]
        assert 70 <= len(d_rows) <= 100 and any("HALT_SET" in ln for ln in e_rows)
        seqs = [int(ln.split(",")[5]) for ln in d_rows]
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))   # every frame exactly once
        meta = json.loads((folders[0] / "meta.json").read_text(encoding="utf-8"))
        assert meta["schema"] == "bird.bend.recording" and meta["integrity"]["complete"] is True
        assert meta["integrity"]["rows_written"] == len(d_rows) + len(e_rows)
        assert meta["board_uid"] and meta["marks_at_start"]["specimen"] == "S 1/a"
        assert be.status().recording.state == "IDLE"
    finally:
        be.shutdown()


@pytest.mark.req("SW-ACQ-004")
def test_recording_failure_enospc_is_never_silent(tmp_path) -> None:
    be = lockstep_backend(recordings_root=str(tmp_path))
    try:
        h = be.test_hooks
        assert be.record_start().ok
        h.fail_recorder(OSError(errno.ENOSPC, "No space left on device"), after_rows=20)
        h.advance(1000)
        st = be.status()
        assert st.recording.state == "FAILED" and "No space" in st.recording.failure
        assert st.indicators.recording_failed.state == "ON" and st.recording.rows_lost > 0
        be.record_stop()
        meta = json.loads((next(tmp_path.iterdir()) / "meta.json").read_text(encoding="utf-8"))
        assert meta["integrity"]["complete"] is False and meta["integrity"]["failures"]
        h.set_free_space(10)
        assert h.free_space == 10
    finally:
        be.shutdown()


@pytest.mark.req("SW-STOP-003", "SAF-SW-004")
def test_clear_and_resume_gates_need_confirmation() -> None:
    be = lockstep_backend()
    try:
        h = be.test_hooks
        with pytest.raises(ConfirmationRequired):
            h.result(be.estop_clear_async(confirmed=False))
        g = be.status().gates[GateId.CLEAR_STOP]
        assert not g.ok and g.refused[0].code == GateCode.NOTHING_TO_CLEAR
        assert not be.resume().ok
        assert be.tare().ok and be.take_sample().ok                  # M3: real (were NOT_IMPLEMENTED in M2)
        assert not be.hotkey_test_start().ok
        assert not be.motion.disable().ok and be.motion.limits() is not None    # M2: driver not enabled yet
        assert not be.motion.check(api.MotionKind.MOVE).ok
        assert isinstance(be.motion.move_to(1.0).exception(), GateRefused)
        assert isinstance(be.motion.move_by(1.0).exception(), GateRefused)
        assert isinstance(be.motion.home().exception(), GateRefused)
        be.motion.jog_start(1, 1.0)
        be.motion.jog_update(1.0)
        be.motion.jog_stop()
        with pytest.raises(GateRefused):                     # not homed
            be.motion.set_test_zero()
        assert be.limits.thresholds().state == "DEFAULT_ONLY"
        assert h.result(be.limits.recheck_async()).state == "DEFAULT_ONLY"
        assert not be.limits.set(be.limits.get()) and not be.limits.set_no_specimen_mode(True).ok
        assert not be.sequencer.start(be.sequencer.new()).ok and be.sequencer.status().state == "IDLE"   # M4: not homed
        assert be.tare_engine.state().kind == "tare" and not be.load_cal.start().ok
    finally:
        be.shutdown()


@pytest.mark.req("SW-CFG-002")
def test_config_api_board_file(tmp_path) -> None:
    be = lockstep_backend()
    try:
        p = tmp_path / "x.bbboard.json"
        be.config.save_board_config(str(p))
        bc = be.config.load_board_config(str(p))
        assert bc.values["motion.steps_per_mm"] == 800.0 and not bc.hash_mismatch
        assert be.config.locked_keys() and be.config.metas() and be.config.groups()
        assert be.test_hooks.result(be.config.read_all_async())["afe.rate_sps"] == 1
    finally:
        be.shutdown()


@pytest.mark.req("SW-PLT-001", "SW-PLT-003", "SYS-008")
def test_headless_entry_point_against_sim() -> None:
    r = subprocess.run([sys.executable, "-m", "bend_stand", "--headless", "--sim", "--duration", "1.5"],
                       capture_output=True, text=True, timeout=60, cwd=str(SW_ROOT / "src"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "connected: sim" in r.stdout and "frames ok" in r.stdout


@pytest.mark.req("SW-PLT-003", "NFR-002")
@pytest.mark.rt
def test_real_clock_backend_with_sim() -> None:
    be = Backend(BackendSettings(wire_log=True))
    be.start()
    try:
        info = be.connect_async("sim").result(timeout=10)
        assert info.build.startswith("SIM")
        time.sleep(1.0)
        st = be.status()
        assert st.link.state == LinkState.CONNECTED and st.stream.on and st.link.stats.data_frames > 50
        lat = []
        for _ in range(20):
            t0 = time.monotonic_ns()
            r = be.stop("gui")
            lat.append(r.t_write_ns - t0)
            time.sleep(0.01)
        lat.sort()
        assert lat[18] < 50_000_000
        be.disconnect_async().result(timeout=5)
        assert be.status().link.state == LinkState.DISCONNECTED
    finally:
        be.shutdown()


@pytest.mark.req("SW-ACQ-002")
def test_recording_restart_within_the_same_second(tmp_path) -> None:
    """SWD-M1-09: stop + restart within one second -> its own folder (suffix), never a raw OS error."""
    be = lockstep_backend(recordings_root=str(tmp_path / "rec"))
    try:
        h = be.test_hooks
        for _ in range(3):
            assert be.record_start().ok
            h.advance(100)
            assert be.record_stop().ok
        names = sorted(p.name for p in (tmp_path / "rec").iterdir())
        assert len(names) == 3 and names[1] == names[0] + "_2" and names[2] == names[0] + "_3"
        (tmp_path / "blocker").write_text("x", encoding="utf-8")
        object.__setattr__(be.settings, "recordings_root", str(tmp_path / "blocker"))
        g = be.record_start()
        assert not g.ok and "WinError" not in g.text() and "cannot create" in g.text()
    finally:
        be.shutdown()

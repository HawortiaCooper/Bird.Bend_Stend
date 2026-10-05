"""Lock-step rig for the M4 sequencer scenario tests (Implementer B): a connected, calibrated, tared, enabled and homed
backend on the in-process simulator with the test travel zero at machine 10 mm and a spring specimen whose contact
point is ``contact_mm`` (test coordinate)."""
from __future__ import annotations

import struct
from typing import Any

from bbs_support import lockstep_backend, tx_frames

from bend_stand.core import protocol_gen as pg
from bend_stand.core.sequencer.model import Loop, Sequence, Step, StepKind

K_SIM = 1 / 3285.0                    # simulator cell: 3285 counts/N (SRS A-02)
CAL = {"created_utc": "2026-10-05T00:00:00Z", "afe": {"type": "HX711", "channel": "A", "gain": 128, "rate_sps": 80},
       "points": [{"mass_kg": 0.0, "force_n": 0.0, "raw_mean": 50_000.0, "raw_std": 45.0},
                  {"mass_kg": 40.0, "force_n": 392.266, "raw_mean": 50_000.0 + 3285 * 392.266, "raw_std": 45.0}],
       "fit": {"k_n_per_count": K_SIM, "status": "UNVERIFIED_LINEARITY"}}
X_ZERO_MM = 10.0
WORLD_OFFSET_UM = -500                # world x = machine x − 0.5 mm after homing (simulator default)
END = ("FINISHED", "STOPPED", "ABORTED", "ERROR")


def ready(tmp_path: Any, *, calibrate: bool = True, no_specimen: bool = False, home: bool = True,
          contact_mm: float | None = 5.0, k_n_mm: float = 50.0, specimen: dict[str, Any] | None = None) -> Any:
    be = lockstep_backend(recordings_root=str(tmp_path / "rec"), no_specimen=no_specimen)
    h = be.test_hooks
    h.advance(50)
    if calibrate:
        be.activate_load_calibration(CAL)
        assert be.tare(window_s=2.0).ok
        assert h.run_until(lambda: be.tare_engine.state().phase == "DONE", 15_000), be.tare_engine.state()
        assert h.run_until(lambda: be.device.thresholds.state == "VERIFIED"
                           and be.device.threshold_mgr.matches(be.device.thresholds), 3000)
    assert be.motion.enable().ok
    assert h.run_until(lambda: be.status().motion.enabled, 2000)
    if home:
        h.result(be.config.write_and_verify_async({"home.v_fast_um_s": 20_000, "home.v_slow_um_s": 2_000}))
        h.result(be.motion.home(load_confirmed=True), 60_000)
        assert h.run_until(lambda: be.status().motion.homed and not be.status().motion.moving, 2000)
        h.result(be.motion.move_to(X_ZERO_MM, speed_mm_s=10.0), 10_000)
        h.advance(100)
        be.motion.set_test_zero()
    if contact_mm is not None:
        kw = dict(kind="spring", k_n_per_mm=k_n_mm,
                  x_contact_um=int(round((X_ZERO_MM + contact_mm) * 1000)) + WORLD_OFFSET_UM)
        kw.update(specimen or {})
        be.sim.act("specimen", **kw)
    h.advance(100)
    return be


def load_seq(*targets: float, k_est: float = 40.0, settle: float = 0.5, capture: float = 1.0, tol: float = 2.0,
             speed: float = 1.0, ret: bool = True, loops: list[Loop] | None = None) -> Sequence:
    steps = [Step("s0", StepKind.TRAVEL, 0.0, speed_mm_s=2.0, label="start")]
    for i, t in enumerate(targets):
        steps.append(Step(f"l{i}", StepKind.LOAD, float(t), speed_mm_s=speed, settle_s=settle, capture_s=capture,
                          tol_n=tol, label=f"{t:g} N"))
    if ret:
        steps.append(Step("r", StepKind.TRAVEL, 0.0, speed_mm_s=2.0, label="return"))
    return Sequence(name="test", steps=steps, loops=loops or [], k_est_n_mm=k_est)


def start(be: Any, seq: Sequence) -> Any:
    g = be.sequencer.start(seq, confirmed=True)
    assert g.ok, g.items
    assert be.sequencer.status().state in ("PREPARING", "RUNNING")
    return g


def run_to_end(be: Any, ms: float = 180_000) -> Any:
    h = be.test_hooks
    assert h.run_until(lambda: be.sequencer.status().state in END and be.sequencer.status().phase == "END", ms), \
        be.sequencer.status()
    h.advance(1500)                       # 1 s recording tail + report
    return be.sequencer.status()


def wire(be: Any, cmd: int) -> list[Any]:
    return tx_frames(be, cmd)


def move_targets(be: Any) -> list[int]:
    return [struct.unpack_from("<i", r.frame, 6)[0] for r in tx_frames(be, pg.Cmd.MOVE_ABS)]


def mul_frames(be: Any) -> list[tuple[int, int, int, int, int]]:
    return [struct.unpack_from("<iIIiB", r.frame, 6) for r in tx_frames(be, pg.Cmd.MOVE_UNTIL_LOAD)]


def stop_modes(be: Any) -> list[int]:
    return [r.frame[6] for r in tx_frames(be, pg.Cmd.STOP)]

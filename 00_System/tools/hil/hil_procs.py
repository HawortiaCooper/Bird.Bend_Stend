"""HG procedures of the hardware gate (FW_test_plan v0.4.1 §6.5, order §6.7, bench procedure §6.8).

Owner: Validator E (00_System/tools/hil, Integrator reviews). Every procedure takes the session context `ctx`
(link, operator, config, recorder) and records Checks (hil_budget) for its HG item. The same code runs
  * on the board (target mode: serial link through the D-06 interlock, ConsoleOperator), and
  * against the FW twin with the Integrator's HW_MEAS model (twin mode: TwinOperator emulates the modelled physical
    actions, everything electrical / optical stays MANUAL; DWT, J-AUX stamps and RC delays are TARGET-ONLY).
Twin results exercise the procedure, the DIAG_MEAS evidence chain and the evaluation; they are NOT hardware evidence.

Verifies (HW gate, target-only criteria): SYS-004/005/006/009/011, SAF-FW-002/004/005/007/018/019/026,
FW-PLT-002, FW-AFE-001/004, FW-TIM-001, FW-MOT-001/008, FW-HOM-001/002/003, FW-NVM-002/003, FW-SW-004,
IF-002, NFR-005/006/007/008 (mapping per item in hil_session.PLAN).
"""
from __future__ import annotations

import json
import math
import random
import statistics
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import hil_budget as hb
from hil_link import PBYKEY, REPO, Link, LinkError, NOT_IN_BUILD  # (also puts 00_System/tools on sys.path)
from hil_operator import Answer, Operator

import ref_codec as rc  # noqa: E402

STOP_CAUSE = rc.STOP_CAUSE
MD = {n: i for i, n in enumerate(rc.MOVE_DONE_REASON)}
FAULT_BIT = {n: i for i, n in enumerate(rc.FAULTS)}


class BenchRefused(RuntimeError):
    """A §6.8 / SYS-009 precondition is not met — the step is not executed."""


# ============================================================================================ recorder
@dataclass
class ItemRec:
    id: str
    title: str
    req: str = ""
    method: str = ""
    checks: list[hb.Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    error: str = ""
    t_start: str = ""
    t_end: str = ""
    operator: list[dict] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        return "ERROR" if self.error else hb.verdict(self.checks)

    def to_json(self) -> dict:
        return {"id": self.id, "title": self.title, "req": self.req, "method": self.method, "verdict": self.verdict,
                "checks": [c.to_dict() for c in self.checks], "notes": self.notes, "data": self.data,
                "error": self.error, "t_start": self.t_start, "t_end": self.t_end, "operator": self.operator}


DEFAULT_CFG: dict[str, Any] = {
    # trial counts (§6.1 rule 3 / §6.5); --quick scales them down for a smoke run (never evidence)
    "n_estop_stim": 100, "n_estop_press": 10, "n_limit_stim": 100, "n_limit_real": 10, "n_pc_stop": 100,
    "n_moves": 100, "n_jogs": 10, "n_rand_stops": 100, "n_caliper": 5, "n_dir_rev": 100, "n_save": 20,
    "n_hang": 10, "n_load_trials": 100, "n_home_rep": 10, "n_cmds": 1000, "soak_s": 600.0, "hx_reads": 10000,
    "pwm_pulses": 100_000, "rel_soak_s": 120.0,
    # bench / geometry
    "bench_window_um": (5_000, 55_000),       # §6.8 P-1 50 mm window (OI-E-HG-02: next to START, re-homing per trial)
    "trial_v_um_s": 30_000, "counts_per_n": 3285.0, "preload_n": 98.0,
    "spring_k_n_per_mm": 20.0, "spring_contact_um": 120_000,     # twin world only
    "buffer_fitted": False, "power_sense_fitted": False,
    "baud_actual": 918_367.0,                  # USART2 BRR at 90 MHz (target); the twin model runs 921 600
    # uncertainties (§6.1 rule 4, R-3 defaults until HG-29 e measured them on the day)
    "u_th_us": {"ESTOP": 1.0, "LIMIT_START": 15.0, "LIMIT_END": 15.0, "DOUT": 1.0, "RX": 1.1, "DRV_PWR": 2000.0},
    "dmm_u_v": 0.02, "dmm_u_ma": 0.1, "caliper_u_mm": 0.02, "dial_u_mm": 0.001,
    "erase_threshold_ms": 100,               # target: a SAVE longer than this included a sector erase
}

QUICK = {"n_estop_stim": 10, "n_estop_press": 3, "n_limit_stim": 10, "n_limit_real": 2, "n_pc_stop": 10,
         "n_moves": 10, "n_jogs": 3, "n_rand_stops": 10, "n_caliper": 2, "n_dir_rev": 10, "n_save": 6, "n_hang": 2,
         "n_load_trials": 10, "n_home_rep": 3, "n_cmds": 150, "soak_s": 60.0, "hx_reads": 800, "pwm_pulses": 20_000,
         "rel_soak_s": 20.0}


class Ctx:
    def __init__(self, link: Link, op: Operator, cfg: dict, out: Path, seed: int = 1, image: str = "meas"):
        self.L, self.op, self.cfg, self.out = link, op, cfg, Path(out)
        self.rng = random.Random(seed)
        self.image = image
        self.item: ItemRec | None = None
        self.shared: dict[str, Any] = {}
        (self.out / "results").mkdir(parents=True, exist_ok=True)

    @property
    def twin(self) -> bool:
        return self.L.is_twin

    @property
    def tw(self):
        return self.L.twin

    # ---------------------------------------------------------------- recording
    def begin(self, item_id: str, title: str, req: str = "", method: str = "") -> ItemRec:
        self.item = ItemRec(item_id, title, req, method, t_start=time.strftime("%Y-%m-%d %H:%M:%S"))
        self.op.item = item_id
        return self.item

    def end(self) -> ItemRec:
        it = self.item
        assert it is not None
        it.t_end = time.strftime("%Y-%m-%d %H:%M:%S")
        it.operator = self.op.take_log(it.id)
        (self.out / "results" / f"{it.id}.json").write_text(json.dumps(it.to_json(), indent=1, default=str),
                                                            encoding="utf-8")
        return it

    def check(self, c: hb.Check) -> hb.Check:
        assert self.item is not None
        self.item.checks.append(c)
        return c

    def note(self, s: str) -> None:
        assert self.item is not None
        self.item.notes.append(s)

    def data(self, k: str, v: Any) -> None:
        assert self.item is not None
        self.item.data[k] = v

    def judged(self, c: hb.Check, *answers: Answer) -> hb.Check:
        """A check computed from operator answers: if any answer is a dry-run default it is MANUAL, not evidence."""
        bad = [a for a in answers if not a.evidence]
        if bad:
            c.decision = hb.MANUAL
            c.note = (c.note + "; " if c.note else "") + f"operator input: {bad[0].source}"
        elif any(a.source.startswith("twin-observed") for a in answers):
            c.note = (c.note + "; " if c.note else "") + "twin-observed (world model, not HW evidence)"
        return self.check(c)

    def result_of(self, item_id: str) -> dict | None:
        f = self.out / "results" / f"{item_id}.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    # ---------------------------------------------------------------- set-up helpers
    def jumpers(self, text: str) -> None:
        self.op.instruct("JUMPERS", "Measurement header MH: " + text, twin=lambda tw: "MH (twin: selector = probe source)")

    def u_th(self, src: str) -> float:
        measured = self.shared.get("u_th_measured", {})
        return float(measured.get(src, self.cfg["u_th_us"].get(src, 1.0)))


# ============================================================================================ FW helpers
def has(st: dict, name: str) -> bool:
    return any(name in st.get(k, []) for k in ("flags", "status", "io", "sys_flags", "faults"))


def wx(tw) -> float:
    return float(tw.act("query", what="world")["x_um_true"])


def block_bits(detail: int) -> list[str]:
    return rc.bits_to_names(detail, rc.BLOCK)


def spm(ctx: Ctx) -> float:
    return float(ctx.L.get("motion.steps_per_mm"))


def wait_done(ctx: Ctx, n0: int, timeout_ms: float, jog_v: int | None = None) -> dict | None:
    return ctx.L.wait_event("MOVE_DONE", n0, timeout_ms, jog_v=jog_v, step_ms=2.0)


def stop_motion(ctx: Ctx) -> None:
    L = ctx.L
    st = L.status()
    if st["motion_state"] in ("JOG", "MOVE_ABS", "HOMING", "MOVE_UNTIL_LOAD", "STOPPING"):
        n0 = L.n_events()
        L.cmd("STOP", {"mode": 1})
        wait_done(ctx, n0, 5000)


def release_estop_input(ctx: Ctx) -> None:
    ctx.op.instruct("ESTOP-RELEASE", "Release the red E-stop (turn to release) if it is pressed",
                    twin=lambda tw: tw.act("estop", open=False, drv_power_follows=False))


def clear_latches(ctx: Ctx) -> dict:
    """ESTOP / HALT / PAUSED / faults cleared if their cause is gone. Returns the final status."""
    L = ctx.L
    stop_motion(ctx)
    st = L.status()
    if "ESTOP" in st["flags"]:
        if "ESTOP_OPEN" in st["io"]:
            release_estop_input(ctx)
        L.wait(int(ctx.L.get("io.estop_release_ms")) + 30)
        L.cmd("ESTOP_CLEAR")
    if "HALT" in st["flags"] or "PAUSED" in st["status"]:
        L.cmd("HALT_CLEAR")
    st = L.status()
    if st["faults"]:
        L.cmd("FAULT_CLEAR")
    return L.status()


def enable(ctx: Ctx) -> None:
    L = ctx.L
    st = L.status()
    if "ENABLED" in st["flags"]:
        return
    r = L.ok("ENABLE")
    L.wait(r["settle_ms"] + 30)
    st = L.status()
    if "ENABLED" not in st["flags"]:
        raise LinkError(f"ENABLE did not complete: {st['motion_state']} {st['flags']}")


def home(ctx: Ctx, timeout_ms: float = 240_000) -> dict:
    L = ctx.L
    n0 = L.n_events()
    r = L.cmd("HOME", {"flags": 0})
    if r["status"] == "E_CONFIRM":
        a = ctx.op.ask_yes_no("HOME-CONFIRM", "HOME load pre-check refused (raw beyond home.max_load_raw). The axis is "
                              "unloaded and the cell offset is the cause — home anyway (flags bit0)?", nominal=True)
        if not a.value:
            raise BenchRefused("HOME refused by the load pre-check, operator declined")
        r = L.cmd("HOME", {"flags": 1})
    if r["status"] != "OK":
        raise LinkError(f"HOME: {r}")
    md = wait_done(ctx, n0, timeout_ms)
    if md is None:
        raise LinkError("HOME: no MOVE_DONE")
    return md


def move_abs(ctx: Ctx, target_um: int, v_um_s: int, wait: bool = True, timeout_ms: float = 120_000) -> dict | None:
    L = ctx.L
    n0 = L.n_events()
    r = L.cmd("MOVE_ABS", {"target_um": int(target_um), "v_um_s": int(v_um_s), "a_um_s2": 0})
    if r["status"] != "OK":
        raise LinkError(f"MOVE_ABS {target_um} @ {v_um_s}: {r}")
    if not wait:
        return None
    md = wait_done(ctx, n0, timeout_ms)
    if md is None:
        raise LinkError(f"MOVE_ABS {target_um}: no MOVE_DONE")
    return md


def jog(ctx: Ctx, v_um_s: int) -> dict:
    return ctx.L.cmd("JOG", {"v_um_s": int(v_um_s), "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})


def jog_stop(ctx: Ctx, timeout_ms: float = 5000) -> dict | None:
    n0 = ctx.L.n_events()
    ctx.L.cmd("JOG", {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
    return wait_done(ctx, n0, timeout_ms)


def leave_limits(ctx: Ctx) -> None:
    """If a limit latch / input is active, jog away from it (un-homed ≤ v_unhomed) until it clears."""
    L = ctx.L
    for _ in range(3):
        st = L.status()
        if "LIMIT_END" in st["status"] or "LIMIT_END" in st["io"]:
            v = -1500
        elif "LIMIT_START" in st["status"] or "LIMIT_START" in st["io"]:
            v = 1500
        else:
            return
        if "ENABLED" not in st["flags"]:
            enable(ctx)
        r = jog(ctx, v)
        if r["status"] != "OK":
            raise LinkError(f"leave_limits: JOG {v}: {r}")
        L.jog_hold(v, 3000)
        jog_stop(ctx)
        L.wait(int(L.get("io.release_ms")) + 30)


def ready(ctx: Ctx, x_um: int | None = None, v_um_s: int = 20_000) -> dict:
    """Precondition R: latches cleared, enabled, homed, at x (FW_test_plan §1.4)."""
    st = clear_latches(ctx)
    leave_limits(ctx)
    st = clear_latches(ctx)
    enable(ctx)
    st = ctx.L.status()
    if "HOMED" not in st["flags"]:
        md = home(ctx)
        if md["arg"] != MD["TARGET"]:
            raise LinkError(f"HOME failed: {md} {ctx.L.status()['faults']}")
    if x_um is not None and ctx.L.status()["pos_um"] != x_um:
        move_abs(ctx, x_um, v_um_s)
    return ctx.L.status()


@contextmanager
def params(ctx: Ctx, values: dict[str, Any]):
    """Session (RAM) parameter changes, restored to the read-back originals afterwards."""
    L = ctx.L
    orig = {k: L.get(k) for k in values}
    try:
        for k, v in values.items():
            L.set_ok(k, v)
        yield orig
    finally:
        for k, v in orig.items():
            try:
                L.set_ok(k, v)
            except Exception as e:  # noqa: BLE001
                ctx.note(f"restore {k}={v} failed: {e}")


def first_bit(names: list[str], table: list[str]) -> int:
    return rc.names_to_bits(names, table)


def stamp(ctx: Ctx, chan: str, entry: int) -> int | None:
    return ctx.L.meas.stamp_entries(chan, entry, entry)[entry]


def probe_idle(ctx: Ctx) -> None:
    """Re-arm the probe in RESET mode (1 µs ticks) like meas_start(): on the board the PWM_INPUT mode switches the
    TIM8 capture DMA off (DIER = CC2IE only), so the MT-4 EVT / PUL / DIR stamps stop until a non-PWM arm."""
    ctx.L.meas.probe_arm("STIM", "RESET", False, 179)


def pwm_measure(ctx: Ctx, v_um_s: int, hold_ms: float, psc: int) -> dict:
    """PWM-input statistics over a jog's cruise phase (armed after the ramp, read before the stop)."""
    L, M = ctx.L, ctx.L.meas
    r = jog(ctx, v_um_s)
    if r["status"] != "OK":
        raise LinkError(f"JOG {v_um_s}: {r}")
    L.jog_hold(v_um_s, 400)
    M.probe_arm("STIM", "PWM_INPUT", False, psc)
    L.jog_hold(v_um_s, hold_ms)
    pr = M.probe_read()
    jog_stop(ctx)
    probe_idle(ctx)
    # cruise only: every genuine period / high width is the same ± 1 tick. A min far below the max is the first
    # capture after arming entering the statistics (board: meas_f4.c TIM8_CC_IRQHandler takes CCR2 = time since
    # arming and a stale CCR1; twin: twin_meas.c keeps min period 0) -> OBS-E-HG-05 / DEF-HG-01
    pr["artifact_p"] = pr["pwm_min_period"] + 2 < pr["pwm_max_period"]
    pr["artifact_h"] = pr["pwm_min_high"] + 2 < pr["pwm_max_high"]
    return pr


def pwm_artifact_check(ctx: Ctx, pr: dict, tag: str) -> None:
    bad = [k for k in ("artifact_p", "artifact_h") if pr[k]]
    if bad:
        ctx.check(hb.Check(f"{tag} PWM statistics free of the first-capture artifact", "min ≈ max during cruise",
                           hb.INCONCL, {k: pr[k] for k in ("pwm_min_period", "pwm_max_period", "pwm_min_high",
                                                          "pwm_max_high")},
                           note="min values corrupted by the first capture after arming (DEF-HG-01 board HAL / "
                                "OBS-E-HG-05 twin model); periods evaluated on the max (constant cruise)"))


# ============================================================================================ image checks
def verify_image(ctx: Ctx, image: str) -> None:
    """Session step: the image the PO flashed is the requested one (GET_INFO, DIAG_MEAS INFO)."""
    L = ctx.L
    inf = L.info()
    ctx.data("info", inf)
    feats = inf["features"]
    ctx.check(hb.info("build", inf["build"]))
    ctx.check(hb.check_eq("param dict hash", inf["param_dict_hash"], "0xB7B0263F", "ICD v0.7.1 dict 5"))
    if image == "release":
        ctx.check(hb.check_true("FEAT_HW_MEAS = 0", "HW_MEAS" not in feats, "release: no HW_MEAS", feats))
        r = L.meas.raw("INFO")
        ctx.check(hb.check_true("DIAG_MEAS -> NOT_IN_BUILD", r["status"] == "E_INTERNAL" and r.get("detail") == NOT_IN_BUILD,
                                "E_INTERNAL detail 1, nothing executed", r))
        if not ctx.twin:
            ctx.check(hb.check_true("build string", not inf["build"].endswith(("-MEAS", "-DWT")), "no -MEAS / -DWT suffix",
                                    inf["build"]))
        return
    ctx.check(hb.check_true("FEAT_HW_MEAS = 1", "HW_MEAS" in feats, "measurement image", feats))
    mi = L.meas.info()
    ctx.data("meas_info", mi)
    ctx.shared["meas_info"] = mi
    want = {"meas": ["MEAS"], "meas_dwt": ["MEAS", "DWT"]}[image]
    if ctx.twin:
        ok = "MEAS" in mi["variant"] and "TWIN_MODEL" in mi["variant"]
        ctx.check(hb.check_true("variant", ok, "twin: MEAS | TWIN_MODEL", mi["variant"]))
        if image == "meas_dwt":
            ctx.check(hb.not_measured("DWT variant", "INFO w0 has DWT", "the twin model has no DWT image (DWT not modelled)"))
    else:
        ctx.check(hb.check_true("variant", all(v in mi["variant"] for v in want) and "TWIN_MODEL" not in mi["variant"],
                                f"INFO w0 = {' | '.join(want)}", mi["variant"]))
        suffix = "-DWT" if image == "meas_dwt" else "-MEAS"
        ctx.check(hb.check_true("build string", inf["build"].endswith(suffix), f"suffix {suffix}", inf["build"]))


# ============================================================================================ phase 1 items
def hg01(ctx: Ctx) -> None:
    op = ctx.op
    a = op.ask_yes_no("C-01", "Board = Stefan's NUCLEO-F446RE (D-22); solder bridges SB13/14 ON, SB62/63 OFF, SB16/50 ON, "
                      "SB54/55 OFF, SB46/52 OFF, SB51/56 ON (pinout.md §5)? Photo taken", nominal=True)
    ctx.judged(hb.check_true("C-01 solder bridges", a.value, "as pinout.md §5"), a)
    ph = op.ask_text("PHOTO", "Photo file name(s) of the board and the MH header", nominal="(dry run)")
    ctx.data("photos", ph.value)
    for j, pin in (("J-PUL-A", "PC7"), ("J-PUL-B", "PB7"), ("J-DIR", "PC9"), ("J-ENA", "PC8"), ("J-EVT", "PC6"),
                   ("J-AUX", "PA11")):
        r = op.ask_number(j, f"DMM resistance from the {j} tap to MCU pin {pin} (MCU unpowered)", "ohm", nominal=1000.0)
        ctx.judged(hb.check_range(f"{j} series R", [r.value or 0], 950, 1100, 5, "ohm", "1 kΩ ± 5 % + wire"), r)
    r = op.ask_number("J-STIM", "DMM resistance PB8 -> J-STIM plug (REQ-A-M2-07: 220 Ω)", "ohm", nominal=220.0)
    ctx.judged(hb.check_range("J-STIM series R", [r.value or 0], 209, 240, 2, "ohm", "220 Ω ± 5 %"), r)


def hg19(ctx: Ctx) -> None:
    a = ctx.op.ask_text("DIP", "Driver DIP SW1..SW8 as seen (8 × on/off, e.g. 'on off on off off on off on')",
                        nominal="on off on off off on off on")
    want = "on off on off off on off on"
    got = " ".join(str(a.value).lower().split())
    ctx.judged(hb.check_eq("DIP setting", got, want, "D-27 target: 4000 p/rev, CCW, assist on, 86-80/86-118 closed loop"), a)
    ctx.check(hb.manual("travel calibration vs 800 steps/mm", "recorded (SW-CAL wizard, M3)",
                        note="first travel calibration result is recorded at HG-25"))


def hg20(ctx: Ctx) -> None:
    op = ctx.op
    for k, q in (("NC", "E-stop NC contact -> input cell -> PA10 (wiring §7)"),
                 ("NO", "E-stop NO contact -> +5V_CUT -> R_E 68 Ω -> D2 -> ENA+ (§2.1)"),
                 ("D1", "D1 Schottky in the MCU ENA path, 10 kΩ pull-down ENA+ -> logic GND"),
                 ("NOK1", "no contactor in the 48 V path (D-41); 48 V PSU mains switch reachable"),
                 ("NOMCU", "the D-42 path contains no MCU-controlled element"),
                 ("LABEL", "panel label / red mushroom on yellow, PAUSE separate")):
        a = op.ask_yes_no(k, f"Inspection: {q}?", nominal=True)
        ctx.judged(hb.check_true(f"circuit {k}", a.value, q), a)
    v = op.ask_number("M-5", "DMM M-5: +5V_CUT at the DC-DC output, 48 V bus ON", "V", nominal=5.0)
    ctx.judged(hb.check_range("M-5 +5V_CUT", [v.value or 0], 4.75, 5.25, ctx.cfg["dmm_u_v"], "V"), v)
    r = op.ask_number("M-5iso", "DMM: resistance 48 V GND <-> logic GND, 48 V bus OFF", "Mohm", nominal=50.0)
    ctx.judged(hb.check_ge("isolation", [r.value or 0], 10.0, 0.0, "MΩ"), r)


def hg31(ctx: Ctx) -> None:
    a = ctx.op.ask_yes_no("INIT", "A's statement / init-code inspection: in EVERY build the MH pins (PC6…PC9, PB7, PA11, PB8) "
                          "are digital inputs without pull (never analog) — pinout §1.5, REQ-A-M2-04?", nominal=True)
    ctx.judged(hb.check_true("MH pins not analog", a.value, "no loopback pin in analog mode while a 5 V tap is fitted"), a)
    ctx.check(hb.manual("release image with jumpers fitted", "repeated at HG-30", note="confirmed when the release image runs"))


# ---------------------------------------------------------------------------------------- HG-29
def hg29(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    # (a) continuity = HG-01
    r01 = ctx.result_of("HG-01")
    if r01 and r01["verdict"] == hb.PASS:
        ctx.check(hb.check_true("a continuity (HG-01)", True, "HG-01 PASS today"))
    else:
        ctx.check(hb.manual("a continuity (HG-01)", "HG-01 PASS today", note=f"HG-01 verdict {r01 and r01['verdict']}"))
    # (b) INFO
    mi = M.info()
    ctx.data("info", mi)
    ctx.check(hb.check_eq("b probe clock", mi["probe_hz"], 180_000_000))
    ctx.check(hb.check_eq("b counter bits", mi["counter_bits"], 32))
    ctx.check(hb.check_eq("b stamp clock", mi["stamp_hz"], 1_000_000))
    ctx.check(hb.check_eq("b ring size", mi["ring"], 2048))
    ctx.check(hb.check_le("b DMA stamp latency", [mi["dma_latency_ns"]], 1000, 0, "ns"))
    ctx.check(hb.check_eq("b stimulus clock", mi["stim_hz"], 10_000_000))
    # (c) STIM self-test: J-EVT = STIM, J-AUX = STIM
    ctx.jumpers("J-EVT <- PB8 (STIM), J-AUX <- PB8 (STIM); J-STIM not plugged into any input")
    psc, n_p, hold = 1799, 5, 10
    tick = hb.probe_tick_us(psc)
    n_evt0, n_aux0 = M.stamp_count("EVT"), M.stamp_count("AUX")
    M.probe_arm("STIM", "TRIGGER", False, psc)
    r = M.stim_run(n_p, hold, False, seed=ctx.rng.randrange(1, 2**31))
    ctx.check(hb.check_eq("c STIM_RUN accepted", r["status"], "OK"))
    L.wait(n_p * (2 * hold + 1) + 30, keepalive=False)
    _, t1 = M.counter()
    pr = M.probe_read()
    _, t2 = M.counter()
    ctx.check(hb.check_true("c probe TRIGGERED by STIM", "TRIGGERED" in pr["flags"], "MT-3 trigger on J-EVT", pr["flags"]))
    ev = M.stamp_entries("EVT", n_evt0, n_evt0 + n_p - 1)
    ts = [ev[i] for i in sorted(ev)]
    ctx.data("stim_evt_stamps", ts)
    if None in ts:
        ctx.check(hb.check_true("c EVT stamps per pulse", False, f"{n_p} rising-edge stamps", ts))
    else:
        iv = [hb.sdiff32(b, a) for a, b in zip(ts, ts[1:])]
        # board (meas_f4.c): the next pulse's random delay starts at the end of the hold (interval = hold + delay);
        # twin model (twin.py _stim): an idle gap of `hold` follows every pulse (interval = 2·hold + delay) — OBS-E-HG-02
        k = 2 if ctx.twin else 1
        ctx.check(hb.check_range(f"c EVT interval = {k}·hold + random delay", iv, k * hold * 1000, k * hold * 1000 + 1000,
                                 hb.u_stamp_pair_us(mi["dma_latency_ns"]), "µs",
                                 "delay 0…1 ms (step timer stopped)" + (" — twin model spacing" if ctx.twin else "")))
        lo, hi = hb.sdiff32(t1, ts[0]), hb.sdiff32(t2, ts[0])
        cnt_us = pr["cnt"] * tick
        ok = (lo - tick - 2) <= cnt_us <= (hi + tick + 2) and "WINDOW_OVERFLOW" not in pr["flags"]
        ctx.check(hb.check_true("c MT-3 CNT vs MT-4 stamps", ok, "probe CNT·tick within [t_read1, t_read2] − first stamp",
                                f"cnt {cnt_us:.1f} µs in [{lo}, {hi}] µs"))
    if ctx.twin:
        ctx.check(hb.target_only("c J-AUX stamps (pulse width = hold)", "hold ± 1 µs", "J-AUX not modelled in the twin"))
    else:
        aux_n = M.stamp_count("AUX") - n_aux0                   # TIM1 CH4 captures both edges (meas_f4.c)
        ctx.check(hb.check_eq("c J-AUX edges (both edges per pulse)", aux_n, 2 * n_p))
        if aux_n == 2 * n_p:
            ax = M.stamp_entries("AUX", n_aux0, n_aux0 + aux_n - 1)
            av = [ax[i] for i in sorted(ax)]
            if None not in av:
                widths = [hb.sdiff32(av[i + 1], av[i]) for i in range(0, len(av), 2)]
                ctx.check(hb.check_range("c J-AUX pulse width = hold", widths, hold * 1000 - 1, hold * 1000 + 1,
                                         hb.u_stamp_pair_us(mi["dma_latency_ns"]), "µs"))
                d_ea = [hb.sdiff32(av[2 * i], ts[i]) for i in range(min(n_p, len(ts)))]
                ctx.check(hb.check_abs_le("c J-EVT vs J-AUX stamp of the same edge", d_ea,
                                          hb.u_stamp_pair_us(mi["dma_latency_ns"]), 0, "µs"))
    # (d) 10 000-step move at 10 kHz with the driver PSU off (OI-E-HG-03)
    with psu_off_pulses(ctx, spm_val=5000.0, v_unhomed=2000):
        _hg29d(ctx)
    # (e) effective RC + threshold delay per input (u_th, R-3)
    if ctx.twin:
        ctx.check(hb.target_only("e RC + threshold delay per input", "recorded (basis of u_th, R-3)",
                                 "needs J-AUX on the pin node; not modelled in the twin (E-stop part in the bench block)"))
    else:
        for src in ("LIMIT_START", "LIMIT_END", "PAUSE"):
            rc_delay(ctx, src)
    # (f) TC-SYS-009-02 report present
    rep = REPO / "02_FW" / "docs" / "FW_test_report_M2.md"
    txt = rep.read_text(encoding="utf-8") if rep.exists() else ""
    ok = "TC-SYS-009-02" in txt and "PASS" in txt[txt.find("TC-SYS-009-02"):txt.find("TC-SYS-009-02") + 400]
    ctx.check(hb.check_true("f TC-SYS-009-02 report", ok, "FW_test_report_M2 lists TC-SYS-009-02 PASS",
                            str(rep.relative_to(REPO))))


def _hg29d(ctx: Ctx) -> None:
    L, M = ctx.L, ctx.L.meas
    probe_idle(ctx)                                   # stamps on (non-PWM probe mode)
    M.counter(reset=True)
    n_pul0 = M.stamp_count("PUL")
    s0 = L.status()["pos_steps"]
    r = jog(ctx, 2000)
    if r["status"] != "OK":
        raise LinkError(f"JOG 2 mm/s: {r}")
    L.jog_hold(2000, 1000)
    jog_stop(ctx)
    c, _ = M.counter()
    s1 = L.status()["pos_steps"]
    n_pul1 = M.stamp_count("PUL")
    ctx.check(hb.check_eq("d MT-2 = |Δpos_steps|", c, abs(s1 - s0)))
    ctx.check(hb.check_eq("d MT-4 stamps = MT-2", n_pul1 - n_pul0, c))
    ctx.check(hb.info("d pulses in the move", c, note="≈ 10 000 (2 mm/s at 5000 steps/mm for ~1 s + ramps)"))
    pr = pwm_measure(ctx, 2000, 600, 0)              # separate move: PWM input stops the stamp DMA on the board
    ctx.data("d", {"counter": c, "dpos": s1 - s0, "stamps": n_pul1 - n_pul0, "probe": pr})
    pwm_artifact_check(ctx, pr, "d")
    vals = [pr["pwm_max_period"]] + ([] if pr["artifact_p"] else [pr["pwm_min_period"]])
    ctx.check(hb.check_range("d PWM-input period at 10 kHz", vals, 17999, 18001, 0, "ticks",
                             f"PSC 0, {pr['pwm_n']} periods during cruise"))


def rc_delay(ctx: Ctx, src: str) -> None:
    """HG-29 e (target): J-STIM at the input connector (switch unplugged), J-EVT = STIM, J-AUX = pin node."""
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    op.instruct(f"RC-{src}", f"Unplug the {src} switch/button; J-STIM -> its input connector; J-EVT <- PB8 (STIM); "
                f"J-AUX <- {src} pin node")
    n_evt0, n_aux0 = M.stamp_count("EVT"), M.stamp_count("AUX")
    M.probe_arm("STIM", "TRIGGER", False, 1799)
    M.stim_run(10, 5, False, seed=ctx.rng.randrange(1, 2**31))
    L.wait(150, keepalive=False)
    evs = [v for v in M.stamp_entries("EVT", n_evt0, n_evt0 + 9).values() if v is not None]
    aux_n = M.stamp_count("AUX") - n_aux0
    aux = [v for v in M.stamp_entries("AUX", n_aux0, n_aux0 + aux_n - 1).values() if v is not None] if aux_n else []
    delays = []
    for e in evs:
        after = [hb.sdiff32(a, e) for a in aux if 0 <= hb.sdiff32(a, e) < 2000]
        if after:
            delays.append(min(after))
    ctx.data(f"e_{src}", {"evt": evs, "aux": aux, "delays_us": delays})
    if delays:
        u = max(delays) + 1.0
        ctx.shared.setdefault("u_th_measured", {})[src] = u
        ctx.check(hb.info(f"e RC delay {src}", max(delays), "µs", f"u_th := {u:.1f} µs for {src}"))
    else:
        ctx.check(hb.check_true(f"e RC delay {src}", False, "J-AUX edges after each STIM edge", aux_n))
    op.instruct(f"RC-{src}-RESTORE", f"Remove J-STIM, reconnect the {src} switch/button")


# ---------------------------------------------------------------------------------------- PSU-off pulse block
@contextmanager
def psu_off_pulses(ctx: Ctx, spm_val: float, v_unhomed: int, psu_back_on: bool = False):
    """Pulse trains above the physical speed envelope are run with the 48 V PSU OFF (OI-E-HG-03): the opto inputs
    are fed from the MCU side, so the electrical load on the PUL / DIR / ENA nodes is unchanged, nothing moves.
    Un-homed JOG (no homing possible without motion) with temporary RAM parameters; a REBOOT without SAVE restores
    the stored parameters afterwards (verified)."""
    L, op = ctx.L, ctx.op
    op.instruct("PSU-OFF", "Switch the 48 V driver PSU OFF (mains switch). Driver signal cable stays connected. "
                "Confirm the motor shaft turns freely (driver unpowered).", twin=lambda tw: tw.act("drv_power", on=False))
    clear_latches(ctx)
    keys = ("motion.steps_per_mm", "motion.v_unhomed_um_s", "drv.alm_active_level")
    orig = {k: L.get(k) for k in keys}
    L.wait(100)
    st = L.status()
    if "ALM" in st["io"]:
        L.set_ok("drv.alm_active_level", 1 - int(orig["drv.alm_active_level"]))
        L.wait(50)
        ctx.note("driver unpowered reads ALM active -> drv.alm_active_level flipped in RAM for the PSU-off block "
                 "(D-28 start-block would refuse the pulse trains); restored by the REBOOT")
    L.set_ok("motion.steps_per_mm", spm_val)
    L.set_ok("motion.v_unhomed_um_s", v_unhomed)
    enable(ctx)
    try:
        yield orig
    finally:
        try:
            stop_motion(ctx)
        except Exception:  # noqa: BLE001
            pass
        L.ok("REBOOT", {"magic": rc.REBOOT_MAGIC})
        L.wait(300, keepalive=False)
        L.cmd("PING")
        back = {k: L.get(k) for k in keys}
        ok = all(abs(float(back[k]) - float(orig[k])) < 1e-6 for k in keys)
        ctx.check(hb.check_true("PSU-off block: parameters restored by REBOOT", ok, "GET_PARAM == originals",
                                {k: (orig[k], back[k]) for k in keys}))
        if psu_back_on:
            op.instruct("PSU-ON", "Switch the 48 V driver PSU back ON; wait 2 s",
                        twin=lambda tw: tw.act("drv_power", on=True))
            L.wait(600)


# ---------------------------------------------------------------------------------------- HG-03 / HG-02
def soak(ctx: Ctx, seconds: float, prefix: str = "") -> dict:
    """Streaming at 80 Hz + 20 cmd/s; MT-5 samples (GET_STATUS t_us vs PC time) once per second."""
    L = ctx.L
    st0 = L.status()
    L.ok("STREAM_START")
    k0 = len(L.frames)
    p0 = dict(L.parser.counters())
    mt5: list[tuple[float, int, int]] = []
    cyc = ("PING", "GET_PARAM", "GET_STATUS", "PING", "GET_PARAM")
    t_start = L.now_ns()
    t_end = t_start + int(seconds * 1e9)
    i = n_sync = 0
    next_s = t_start
    while L.now_ns() < t_end:
        if L.now_ns() >= next_s:
            r = L.cmd("GET_STATUS", timeout_ms=1000)
            n_sync += 1
            t_mid = r["_pc_ns"] - r["_rtt_ms"] * 1e6 / 2
            mt5.append((t_mid / 1e9, r["board_status"]["t_us"], r["board_status"]["uptime_ms"]))
            next_s += int(1e9)
        name = cyc[i % len(cyc)]
        f = {"id": PBYKEY["afe.rate_sps"].id} if name == "GET_PARAM" else None
        L.send(name, f)
        i += 1
        L.advance(50.0)
    L.advance(200.0)
    resp = [rc.decode_frame(rf.frame)["status"] for rf in L.frames[k0:]
            if rf.frame.type & rc.RESP_BIT and rf.frame.type < 0xC0]
    want = {"answered": len(resp)}
    data = L.data_since(k0)
    seqs = [d["frame_seq"] for d in data]
    gaps = sum(((b - a) & 0xFFFF) - 1 for a, b in zip(seqs, seqs[1:]) if ((b - a) & 0xFFFF) != 1)
    st1 = L.status()
    p1 = L.parser.counters()
    fw = {k: st1[k] - st0[k] for k in ("rx_crc_errors", "rx_frame_errors", "rx_overruns", "tx_drops", "event_overflows")}
    res = {"seconds": (L.now_ns() - t_start) / 1e9, "cmds": i + n_sync, "answered": want["answered"],
           "nack": sum(1 for x in resp if x != "OK"), "data": len(data), "seq_gaps": gaps,
           "pc_crc": p1["crc_errors"] - p0["crc_errors"], "pc_len": p1["len_errors"] - p0["len_errors"],
           "pc_timeouts": p1["timeout_drops"] - p0["timeout_drops"], "fw": fw, "mt5": mt5,
           "clk_fallback": "CLK_FALLBACK" in st1["sys_flags"]}
    return res


def hg03(ctx: Ctx) -> None:
    secs = ctx.cfg["soak_s"]
    res = soak(ctx, secs)
    ctx.shared["mt5"] = res["mt5"]
    ctx.shared["soak_clk"] = res["clk_fallback"]
    ctx.data("soak", {k: v for k, v in res.items() if k != "mt5"})
    if secs < 600:
        ctx.note(f"soak shortened to {secs:.0f} s (quick mode) — not the 10 min of C-03")
    ctx.check(hb.check_ge("duration", [res["seconds"]], 600.0, 0, "s") if secs >= 600 else
              hb.info("duration", res["seconds"], "s", "quick mode"))
    ctx.check(hb.check_eq("DATA frame_seq gaps", res["seq_gaps"], 0, f"{res['data']} DATA frames"))
    ctx.check(hb.check_eq("PC CRC / length errors", res["pc_crc"] + res["pc_len"], 0))
    ctx.check(hb.check_eq("FW rx_crc/frame errors, overruns, tx_drops", sum(res["fw"].values()) - res["fw"]["event_overflows"], 0,
                          str(res["fw"])))
    ctx.check(hb.check_eq("all commands answered", res["answered"], res["cmds"], f"{res['nack']} NACK"))
    ctx.check(hb.check_ge("DATA rate", [res["data"] / max(res["seconds"], 1e-9)], 79.0, 0.5, "Hz",
                          note="80 SPS stream (D-04)"))


def regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else float("nan")
    return b, my - b * mx


def hg02(ctx: Ctx) -> None:
    L = ctx.L
    st = L.status()
    clk_ev = [e for e in L.events() if e["code"] == "CLK_FALLBACK"]
    ctx.check(hb.check_true("a no CLK_FALLBACK", "CLK_FALLBACK" not in st["sys_flags"] and not clk_ev and
                            not ctx.shared.get("soak_clk", False), "sys_flags + EVENTs", st["sys_flags"]))
    mt5 = ctx.shared.get("mt5") or soak(ctx, ctx.cfg["soak_s"])["mt5"]
    if len(mt5) >= 3:
        xs = [m[0] - mt5[0][0] for m in mt5]
        ys, prev, off = [], mt5[0][1], 0
        for m in mt5:                                     # unwrap t_us
            if m[1] < prev:
                off += 1 << 32
            prev = m[1]
            ys.append((m[1] + off) / 1e6)
        b, _ = regression(xs, [y - ys[0] for y in ys])
        err_ppm = (b - 1.0) * 1e6
        span = xs[-1]
        ctx.data("mt5", {"n": len(mt5), "span_s": span, "slope": b, "err_ppm": err_ppm})
        ctx.check(hb.check_abs_le("b device clock error vs PC (MT-5)", [err_ppm], 1000.0, 100.0, "ppm",
                                  f"{len(mt5)} samples over {span:.0f} s" + (" (twin: virtual clock = PC clock)" if ctx.twin else "")))
        if span < 600:
            ctx.note(f"MT-5 span {span:.0f} s < 600 s (quick mode)")
    else:
        ctx.check(hb.not_measured("b device clock error vs PC (MT-5)", "≤ 0.1 % (u 0.01 %)", "no soak samples"))
    with psu_off_pulses(ctx, spm_val=2500.0, v_unhomed=20000):
        pr = pwm_measure(ctx, 20000, 500, 0)
        ctx.data("c_probe", pr)
        pwm_artifact_check(ctx, pr, "c")
        vals = [pr["pwm_max_period"]] + ([] if pr["artifact_p"] else [pr["pwm_min_period"]])
        ctx.check(hb.check_range("c PUL period at 50 kHz (PSC 0)", vals, 3599, 3601, 0, "ticks",
                                 f"{pr['pwm_n']} periods during cruise; 1800 TIM2 ticks exact"))


# ============================================================================================ phase 2 items
def hg32(ctx: Ctx, repeat: bool = False) -> None:
    L, op = ctx.L, ctx.op
    ctx.check(hb.check_eq("drv.pwr_sense_enable default", int(L.get("drv.pwr_sense_enable")), 0, "CR-03"))
    ctx.check(hb.check_eq("drv.k1_check_enable default", int(L.get("drv.k1_check_enable")), 0, "D-43 e"))
    clear_latches(ctx)
    enable(ctx)
    n0 = L.n_events()
    op.instruct("PRESS-HOLD", "Press and HOLD the red E-stop (≥ 2 s)",
                twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False))
    L.wait(1500)
    st = L.status()
    evs = [e["code"] for e in L.events_since(n0)]
    ctx.check(hb.check_true("E-stop held > 1 s: only ESTOP", "ESTOP" in st["flags"] and not st["faults"] and
                            "FAULT_SET" not in evs, "ESTOP latched, no K1_WELDED / fault", {"flags": st["flags"],
                                                                                          "faults": st["faults"]}))
    r = L.cmd("ENABLE")
    bits = block_bits(r.get("detail", 0)) if r["status"] == "E_STATE" else []
    ctx.check(hb.check_true("ENABLE refused by ESTOP only", r["status"] == "E_STATE" and bits == ["ESTOP"],
                            "E_STATE BLOCK = ESTOP (no DRV_UNPOWERED)", r))
    release_estop_input(ctx)
    L.wait(int(L.get("io.estop_release_ms")) + 50)
    r = L.cmd("ESTOP_CLEAR")
    ctx.check(hb.check_eq("ESTOP_CLEAR after release", r["status"], "OK"))


def hg06(ctx: Ctx, label: str = "") -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    stop_motion(ctx)
    st = L.status()
    if st["motion_state"] != "NOT_ENABLED":
        L.ok("DISABLE")
    buf = ctx.cfg["buffer_fitted"] or label == "buffer"
    lo, hi = (10.0, 13.0) if buf else (6.0, 1e9)
    for pin in ("PUL", "DIR", "ENA"):
        if pin != "ENA":
            r = M.static_level(pin, 1)
            ctx.check(hb.check_eq(f"{pin} STATIC_LEVEL accepted", r["status"], "OK"))
            if ctx.twin:
                e = [x for x in ctx.tw.act("query", what="edges")["edges"] if x["pin"] == pin]
                ctx.check(hb.check_true(f"{pin} driven high (twin edge log)", bool(e) and e[-1]["level"] == 1,
                                        "pin level 1 while STATIC_LEVEL", e[-1] if e else None))
            what = f"{pin} held high by DIAG_MEAS STATIC_LEVEL"
        else:
            what = "ENA at the disabled level (NOT_ENABLED = LED current)"
        v = op.ask_number(f"{pin}-VOH", f"{what}: DMM VOH at the driver terminal {pin}+ vs logic GND", "V",
                          nominal=4.9 if buf else 3.1)
        i = op.ask_number(f"{pin}-ILED", f"{what}: DMM voltage over the 100 Ω shunt in series with {pin}−", "mV",
                          nominal=1150.0 if buf else 680.0)
        i_ma = (i.value or 0) / 100.0
        if not buf:
            ctx.judged(hb.check_ge(f"{pin} VOH", [v.value or 0], 3.0, ctx.cfg["dmm_u_v"], "V"), v)
            ctx.judged(hb.check_ge(f"{pin} I_LED", [i_ma], lo, ctx.cfg["dmm_u_ma"], "mA", note="bring-up ≥ 6 mA"), i)
        else:
            ctx.judged(hb.check_range(f"{pin} I_LED (buffer)", [i_ma], lo, hi, ctx.cfg["dmm_u_ma"], "mA"), i)
        L.cmd("GET_STATUS")                                           # any command releases STATIC_LEVEL


def hg10cd(ctx: Ctx) -> None:
    """HG-10 c/d — D-42 hardwired ENA cut verified BEFORE any E-stop-sense bypass (§6.8 P-2)."""
    L, op = ctx.L, ctx.op
    clear_latches(ctx)
    enable(ctx)
    a1 = op.ask_yes_no("c1", "Hold the MCU in reset (Nucleo B2 RESET held / NRST jumper to GND). Press the red E-stop. "
                       "Turn the motor shaft by hand: is it FREE (no holding torque)?", nominal=True)
    ctx.judged(hb.check_true("c1 MCU in reset + E-stop -> motor free", a1.value, "D-42 NO contact disables the driver"), a1)
    m1 = op.ask_number("M-1", "Still in reset + pressed: DMM M-1 voltage across R_E (68 Ω)", "V", nominal=0.70)
    ctx.judged(hb.check_ge("d M-1 ENA cut current", [(m1.value or 0) / 68.0 * 1000], 7.0, ctx.cfg["dmm_u_ma"], "mA",
                           note="≥ the driver's ENA threshold (ASSUMED ≥ 7 mA, wiring §11.1)"), m1)
    m3 = op.ask_number("M-3b", "Still in reset + pressed: DMM M-3 at PA4 (CN8-3)", "V", nominal=0.2)
    ctx.judged(hb.check_le("d M-3 PA4 (reset, pressed)", [m3.value or 0], 3.4, ctx.cfg["dmm_u_v"], "V"), m3)
    m4 = op.ask_number("M-4p", "Still in reset + pressed: DMM M-4 at ENA+", "V", nominal=4.3)
    ctx.judged(hb.check_range("d M-4 ENA+ (pressed)", [m4.value or 0], 4.0, 4.6, ctx.cfg["dmm_u_v"], "V"), m4)
    a2 = op.ask_yes_no("c2", "Still in reset: RELEASE the E-stop. Shaft HOLDING again (ENA pull-down = enabled, D-13)?",
                       nominal=True)
    ctx.judged(hb.check_true("c2 reset + released -> holding (D-13)", a2.value, "hardware only, no MCU involved"), a2)
    op.instruct("c3", "Release the MCU reset; wait 2 s (the session reconnects)",
                twin=lambda tw: tw.act("reset", cause="pin"))
    L.wait(300)
    clear_latches(ctx)
    enable(ctx)
    m2a = op.ask_number("M-2a", "MCU running, ENABLED, E-stop released: DMM M-2 ENA loop current (100 Ω shunt in ENA−)",
                        "mA", nominal=0.0)
    ctx.judged(hb.check_le("d M-2a enabled + released", [m2a.value or 0], 0.5, ctx.cfg["dmm_u_ma"], "mA"), m2a)
    n0 = L.n_events()
    op.instruct("c3-press", "MCU running: press the red E-stop",
                twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False))
    L.wait(100)
    st = L.status()
    ctx.check(hb.check_true("c3 FW latched ESTOP, ENA disabled", "ESTOP" in st["flags"] and "ENA_DISABLED" in st["io"],
                            "ESTOP + io ENA_DISABLED", {"flags": st["flags"], "io": st["io"]}))
    m2c = op.ask_number("M-2c", "Pressed, MCU driving disabled: DMM M-2 ENA loop current", "mA", nominal=12.0)
    ctx.judged(hb.check_le("d M-2c no over-current", [m2c.value or 0], 13.5, ctx.cfg["dmm_u_ma"], "mA"), m2c)
    m3c = op.ask_number("M-3c", "Pressed, MCU driving disabled: DMM M-3 at PA4", "V", nominal=3.3)
    ctx.judged(hb.check_le("d M-3 PA4 never 5 V", [m3c.value or 0], 3.4, ctx.cfg["dmm_u_v"], "V"), m3c)
    release_estop_input(ctx)
    L.wait(300)
    st = L.status()
    free = op.ask_yes_no("c3-free", "E-stop released, MCU running: shaft still FREE (FW keeps ENA disabled until "
                         "ESTOP_CLEAR + ENABLE)?", twin=lambda tw: not tw.act("query", what="outputs")["ena_enabled"],
                         nominal=True)
    ctx.judged(hb.check_true("c3 release never re-energises", free.value and "ESTOP" in st["flags"],
                             "ESTOP latched, driver disabled after release", st["flags"]), free)
    L.ok("ESTOP_CLEAR")
    enable(ctx)
    hold = op.ask_yes_no("c3-hold", "After ESTOP_CLEAR + ENABLE: shaft HOLDING?",
                         twin=lambda tw: tw.act("query", what="outputs")["ena_enabled"], nominal=True)
    ctx.judged(hb.check_true("c3 holding only after ESTOP_CLEAR + ENABLE", hold.value, "D-41 restart sequence"), hold)
    ctx.data("events", [e["code"] for e in L.events_since(n0)])


def hg28(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    clear_latches(ctx)
    leave_limits(ctx)
    enable(ctx)
    op.instruct("PREP", "No specimen, nothing in the travel; caliper / dial on the table; hand at the E-stop")
    for attempt in range(2):
        x0 = wx(ctx.tw) if ctx.twin else 0.0
        M.counter(reset=True)
        s0 = L.status()["pos_steps"]
        r = jog(ctx, 1000)
        if r["status"] != "OK":
            raise LinkError(f"first jog: {r}")
        L.jog_hold(1000, 3000)
        jog_stop(ctx)
        c, _ = M.counter()
        s1 = L.status()["pos_steps"]
        sp = spm(ctx)
        cmd_mm = (s1 - s0) / sp
        d = op.ask_yes_no("DIR", "Did the table move AWAY from the START switch (+x)?",
                          twin=lambda tw: wx(tw) > x0, nominal=True)
        cal = op.ask_number("CAL", "Caliper: travel of this jog", "mm", twin=lambda tw: abs(wx(tw) - x0) / 1000.0,
                            nominal=round(abs(cmd_mm), 3))
        ctx.check(hb.check_eq("MT-2 = |Δpos_steps| (first jog)", c, abs(s1 - s0)))
        if d.value or attempt:
            break
        cur = int(L.get("motion.dir_invert"))
        L.set_ok("motion.dir_invert", 1 - cur)
        ctx.note(f"direction wrong: motion.dir_invert {cur} -> {1 - cur} (SAVE after the HG-28 pass)")
        ctx.shared["dir_invert_changed"] = True
    ctx.judged(hb.check_true("direction +x away from START", d.value, "SYS-009 first motion"), d)
    err = abs((cal.value or 0) - abs(cmd_mm)) / max(abs(cmd_mm), 1e-9)
    ctx.judged(hb.check_le("scale (caliper vs commanded)", [err * 100], 10.0, ctx.cfg["caliper_u_mm"] / max(abs(cmd_mm), 1e-9) * 100,
                           "%", note=f"commanded {cmd_mm:.3f} mm at {sp:g} steps/mm"), cal)
    md = home(ctx)
    st = L.status()
    ctx.check(hb.check_true("HOME at START", md["arg"] == MD["TARGET"] and "HOMED" in st["flags"], "HOMED, MOVE_DONE TARGET",
                            md))
    # deliberately wrong DIR: HOME must fail with HOME_WIRING / HOME_NOT_FOUND within home.max_travel_um
    a = op.ask_yes_no("INV-OK", "Next: HOME with motion.dir_invert deliberately WRONG — the table runs AWAY from START "
                      "toward the END switch (no load, ≤ home.v_fast). Hand at the E-stop. Proceed?", nominal=True)
    if a.value:
        cur = int(L.get("motion.dir_invert"))
        L.set_ok("motion.dir_invert", 1 - cur)
        x0 = wx(ctx.tw) if ctx.twin else None
        p0 = st["pos_um"]
        n0 = L.n_events()
        r = L.cmd("HOME", {"flags": 0})
        md = wait_done(ctx, n0, 240_000)
        st2 = L.status()
        L.set_ok("motion.dir_invert", cur)
        hf = L.events_since(n0, "HOME_FAILED")
        travel = abs(wx(ctx.tw) - x0) if ctx.twin else abs(st2["pos_um"] - p0)
        ok = bool(hf) and any(f in st2["faults"] for f in ("HOME_WIRING", "HOME_NOT_FOUND"))
        ctx.check(hb.check_true("inverted DIR -> HOME_WIRING / HOME_NOT_FOUND", ok, "fault latched, HOME_FAILED",
                                {"faults": st2["faults"], "home_failed": hf[:1]}))
        ctx.check(hb.check_le("inverted DIR travel", [travel / 1000], int(L.get("home.max_travel_um")) / 1000, 0.1, "mm",
                              note="twin: world travel" if ctx.twin else "FW position change (operator: compare scale)"))
        L.cmd("FAULT_CLEAR")
        leave_limits(ctx)
        clear_latches(ctx)
        md = home(ctx)
        ctx.check(hb.check_true("re-HOME after the inverted test", md["arg"] == MD["TARGET"], "HOMED", md))
    else:
        ctx.check(hb.manual("inverted DIR -> HOME_WIRING / HOME_NOT_FOUND", "fault within home.max_travel_um",
                            note="operator declined"))
    if ctx.shared.get("dir_invert_changed"):
        s = op.ask_yes_no("SAVE", "motion.dir_invert was changed — SAVE_PARAMS now (persistent commissioning value)?",
                          nominal=True)
        if s.value:
            L.ok("SAVE_PARAMS", timeout_ms=5000)


def hg07(ctx: Ctx, label: str = "") -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    pre = f"{label} " if label else ""
    ctx.jumpers("J-EVT <- PA3 (USART2 RX), J-ENA, J-PUL-A, J-DIR fitted")
    clear_latches(ctx)
    stop_motion(ctx)
    if L.status()["motion_state"] != "NOT_ENABLED":
        L.ok("DISABLE")
    free = op.ask_yes_no("FREE", "NOT_ENABLED (DISABLE sent): motor shaft FREE by hand (no load!)?",
                         twin=lambda tw: not tw.act("query", what="outputs")["ena_enabled"], nominal=True)
    ctx.judged(hb.check_true(f"{pre}disabled = shaft free", free.value, "ENA LED current = driver disabled"), free)
    psc = 1799
    tick = hb.probe_tick_us(psc)
    n_evt0, n_pul0, n_dir0 = M.stamp_count("EVT"), M.stamp_count("PUL"), M.stamp_count("DIR")
    M.probe_arm("RX", "TRIGGER", True, psc)
    r = L.ok("ENABLE")                                   # the very next frame after arming = the trigger
    jb = jog(ctx, 1000)
    ctx.check(hb.check_true(f"{pre}JOG during ENA settle refused", jb["status"] == "E_BUSY" and jb.get("detail") == 2,
                            "E_BUSY ENABLING", jb))
    L.wait(r["settle_ms"] + 50)
    hold = op.ask_yes_no("HOLD", "After ENABLE + settle: shaft HOLDING?",
                         twin=lambda tw: tw.act("query", what="outputs")["ena_enabled"], nominal=True)
    ctx.judged(hb.check_true(f"{pre}enabled = holding", hold.value, "no ENA current = enabled"), hold)
    pr = M.probe_read()
    j = jog(ctx, 1000)
    if j["status"] != "OK":
        raise LinkError(f"JOG after settle: {j}")
    L.jog_hold(1000, 150)
    jog_stop(ctx)
    t_evt = stamp(ctx, "EVT", n_evt0)
    t_pul = stamp(ctx, "PUL", n_pul0)
    n_dir1 = M.stamp_count("DIR")
    t_dir = stamp(ctx, "DIR", n_dir0) if n_dir1 > n_dir0 else None
    ctx.data("probe", pr)
    if t_evt is None or t_pul is None or pr["ccr3"] == 0 or "TRIGGERED" not in pr["flags"]:
        ctx.check(hb.check_true(f"{pre}ENA edge -> first PUL", False, "probe triggered by the ENABLE frame, ENA captured",
                                {"probe": pr, "t_evt": t_evt, "t_pul": t_pul}))
        return
    t_ena = t_evt + pr["ccr3"] * tick
    settle_us = int(L.get("motion.ena_settle_ms")) * 1000
    u = tick + hb.u_stamp_pair_us(ctx.shared.get("meas_info", {}).get("dma_latency_ns", 1000))
    firsts = [hb.sdiff32(t_pul, int(t_ena))] + ([hb.sdiff32(t_dir, int(t_ena))] if t_dir is not None else [])
    ctx.check(hb.check_ge(f"{pre}first PUL/DIR edge − ENA enable edge", firsts, settle_us, u, "µs",
                          note=f"motion.ena_settle_ms = {settle_us // 1000}; ENA edge {pr['ccr3'] * tick:.0f} µs after the "
                               f"ENABLE frame"))


def hg08(ctx: Ctx, label: str = "") -> None:
    L, M = ctx.L, ctx.L.meas
    pre = f"{label} " if label else ""
    ctx.jumpers("J-PUL-A, J-DIR fitted; J-EVT <- DIR node")
    # (a) ≥ 1e5 pulses at 50 kHz, PWM input PSC 1 (11.1 ns), PSU off (OI-E-HG-03)
    psc = 1
    tick = hb.probe_tick_us(psc)
    with psu_off_pulses(ctx, spm_val=2500.0, v_unhomed=20000, psu_back_on=True):
        pr = pwm_measure(ctx, 20000, ctx.cfg["pwm_pulses"] / 50.0 + 100, psc)
    ctx.data("a_probe", pr)
    pwm_artifact_check(ctx, pr, f"{pre}a")
    hi_max = pr["pwm_max_high"] * tick
    p_min = (pr["pwm_max_period"] if pr["artifact_p"] else pr["pwm_min_period"]) * tick
    ctx.check(hb.check_ge(f"{pre}a pulses measured", [pr["pwm_n"]], ctx.cfg["pwm_pulses"], 0, "pulses"))
    if pr["artifact_h"]:
        ctx.check(hb.Check(f"{pre}a PUL high", "min − u ≥ 10 µs", hb.INCONCL, hi_max, "µs", tick, 10.0, pr["pwm_n"],
                           "min high corrupted by the first capture; value shown = max high (DEF-HG-01)"))
    else:
        ctx.check(hb.check_ge(f"{pre}a PUL high", [pr["pwm_min_high"] * tick], 10.0, tick, "µs"))
    ctx.check(hb.check_ge(f"{pre}a PUL low (period_min − high_max)", [p_min - hi_max], 10.0, 2 * tick, "µs"))
    ctx.check(hb.check_ge(f"{pre}a step rate ≤ 50 kHz (period)", [p_min], 20.0, tick, "µs"))
    # (b) DIR setup on ≥ 100 reversals: 1-step MOVE_ABS back and forth, MT-3 trigger on DIR (fine) + MT-4 (coarse)
    ready(ctx, 20_000)
    x0 = L.status()["pos_um"]
    d_um = 0
    for d in (1, 2, 3):
        M.counter(reset=True)
        move_abs(ctx, x0 + d, 1000)
        c, _ = M.counter()
        move_abs(ctx, x0, 1000)
        if c == 1:
            d_um = d
            break
    if not d_um:
        ctx.check(hb.check_true(f"{pre}b 1-step move found", False, "a target delta giving exactly 1 step"))
        return
    # DIR edge -> next PUL rising edge = dir_setup + the first ramp period from rest (PWM mode 2: the edge comes at
    # the end of the first period, ≈ sqrt(2/a) ≈ 5 ms at a_max) -> PSC 17 (0.1 µs, 6.55 ms window), PSC 179 if needed
    pol = {+1: False, -1: True}            # DIR edge polarity per direction, learned on the first trials
    fine, coarse, bad = [], [], 0
    psc_b = 17
    for i in range(ctx.cfg["n_dir_rev"]):
        dirn = +1 if i % 2 == 0 else -1
        tgt = x0 + d_um if dirn > 0 else x0
        for attempt in range(3):
            n_dir0, n_pul0 = M.stamp_count("DIR"), M.stamp_count("PUL")
            M.probe_arm("DIR", "TRIGGER", pol[dirn], psc_b)
            M.counter(reset=True)
            move_abs(ctx, tgt, 1000)
            c, _ = M.counter()
            pr = M.probe_read()
            retry = False
            if "TRIGGERED" not in pr["flags"]:
                pol[dirn] = not pol[dirn]                   # learn the polarity
                retry = True
            elif "WINDOW_OVERFLOW" in pr["flags"] and psc_b == 17:
                psc_b = 179                                 # first period longer than the 6.55 ms window
                retry = True
            if not retry or attempt == 2:
                break
            move_abs(ctx, x0 + d_um if dirn < 0 else x0, 1000)     # undo, same reversal again
        if "TRIGGERED" not in pr["flags"] or "WINDOW_OVERFLOW" in pr["flags"] or c != 1:
            bad += 1
            continue
        fine.append(pr["ccr2"] * hb.probe_tick_us(psc_b))
        td, tp = stamp(ctx, "DIR", n_dir0), stamp(ctx, "PUL", n_pul0)
        if td is not None and tp is not None:
            coarse.append(hb.sdiff32(tp, td))
    tick_b = hb.probe_tick_us(psc_b)
    ctx.data("b", {"delta_um": d_um, "fine_us": fine[:20], "coarse_us": coarse[:20], "invalid": bad})
    setup = float(L.get("motion.dir_setup_us"))
    ctx.check(hb.check_ge(f"{pre}b DIR setup (MT-3, 1 PUL per reversal)", fine, setup, tick_b, "µs",
                          note=f"{len(fine)} reversals, {bad} invalid"))
    ctx.check(hb.check_ge(f"{pre}b DIR setup (MT-4 stamps, cross-check)", coarse, setup,
                          hb.u_stamp_pair_us(), "µs"))
    if fine and coarse:
        diff = [abs(a - b) for a, b in zip(fine, coarse)]
        ctx.check(hb.check_le(f"{pre}b MT-3 vs MT-4 agree", diff, 2.0 + tick_b, 0, "µs"))
    ctx.check(hb.check_ge(f"{pre}b reversals", [len(fine)], ctx.cfg["n_dir_rev"], 0, "trials"))


def hg09(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    rng = ctx.rng
    ready(ctx, 50_000)
    pairs, cal_pairs = [], []
    for i in range(ctx.cfg["n_moves"]):
        tgt = rng.randrange(10_000, 150_000)
        v = rng.randrange(1_000, 30_001)
        M.counter(reset=True)
        s0 = L.status()["pos_steps"]
        x0 = wx(ctx.tw) if ctx.twin else 0
        move_abs(ctx, tgt, v)
        c, _ = M.counter()
        s1 = L.status()["pos_steps"]
        pairs.append((c, abs(s1 - s0)))
        if i < ctx.cfg["n_caliper"]:
            sp = spm(ctx)
            a = op.ask_number(f"CAL{i}", "Caliper: travel of the last move", "mm",
                              twin=lambda tw: abs(wx(tw) - x0) / 1000.0, nominal=abs(s1 - s0) / sp)
            cal_pairs.append((a, abs(s1 - s0) / sp))
    ctx.check(hb.check_all_eq("moves: MT-2 = |Δpos_steps|", pairs, f"{len(pairs)} random MOVE_ABS"))
    for k, (a, cmd_mm) in enumerate(cal_pairs):
        u = ctx.cfg["caliper_u_mm"] + 1.0 / spm(ctx)
        ctx.judged(hb.check_abs_le(f"caliper move {k}", [(a.value or 0) - cmd_mm], 0.0 + u, 0, "mm",
                                   note="|caliper − commanded| ≤ caliper u + 1 step"), a)
    # jogs with reversals: per-direction segments from the PUL / DIR stamps
    jp, jfail = [], []
    for i in range(ctx.cfg["n_jogs"]):
        move_abs(ctx, 80_000, 20_000)
        M.counter(reset=True)
        n_pul0, n_dir0 = M.stamp_count("PUL"), M.stamp_count("DIR")
        s0 = L.status()["pos_steps"]
        sign = 1
        for seg in range(3):
            v = sign * rng.randrange(2_000, 5_001)
            jog(ctx, v)
            L.jog_hold(v, rng.uniform(30, 80) + 60)
            sign = -sign
        jog_stop(ctx)
        c, _ = M.counter()
        s1 = L.status()["pos_steps"]
        n_pul1, n_dir1 = M.stamp_count("PUL"), M.stamp_count("DIR")
        puls = M.stamp_entries("PUL", n_pul0, n_pul1 - 1) if n_pul1 > n_pul0 else {}
        dirs = M.stamp_entries("DIR", n_dir0, n_dir1 - 1) if n_dir1 > n_dir0 else {}
        if None in puls.values() or None in dirs.values():
            ok = c >= abs(s1 - s0) and (c - abs(s1 - s0)) % 2 == 0
            jfail.append(None if ok else (i, c, s1 - s0))
            ctx.note(f"jog {i}: stamp ring overrun, parity check only")
            continue
        dts = sorted(dirs.values(), key=lambda t: hb.sdiff32(t, list(puls.values())[0]))
        segs = [0] * (len(dts) + 1)
        for t in puls.values():
            k = sum(1 for d in dts if hb.sdiff32(t, d) > 0)
            segs[k] += 1
        signed = sum(n if j % 2 == 0 else -n for j, n in enumerate(segs))
        jp.append((c, sum(segs)))
        if abs(signed) != abs(s1 - s0):
            jfail.append((i, segs, s1 - s0))
    ctx.check(hb.check_all_eq("jogs: MT-2 = Σ segments (stamps)", jp, f"{len(jp)} jogs with 2 reversals"))
    ctx.check(hb.check_true("jogs: |Σ ± segments| = |Δpos_steps|", not [f for f in jfail if f], "per direction segment",
                            jfail[:3]))
    # random STOP / HALT stops
    sp_pairs = []
    for i in range(ctx.cfg["n_rand_stops"]):
        st = clear_latches(ctx)
        if "HOMED" not in st["flags"]:
            ready(ctx)
        M.counter(reset=True)
        s0 = L.status()["pos_steps"]
        tgt = 150_000 if s0 < 80_000 * spm(ctx) / 800 else 10_000
        n0 = L.n_events()
        L.ok("MOVE_ABS", {"target_um": tgt, "v_um_s": rng.randrange(5_000, 30_001), "a_um_s2": 0})
        L.wait(rng.uniform(50, 400))
        kind = rng.choice(("STOP0", "STOP1", "HALT"))
        L.cmd("HALT") if kind == "HALT" else L.cmd("STOP", {"mode": int(kind[-1])})
        wait_done(ctx, n0, 10_000)
        st = L.status()
        c, _ = M.counter()
        tol = 1 if "POS_UNCERTAIN" in st["status"] else 0
        sp_pairs.append((c, abs(st["pos_steps"] - s0)) if not tol else (c, c if abs(c - abs(st["pos_steps"] - s0)) <= 1
                                                                        else abs(st["pos_steps"] - s0)))
        if kind == "HALT":
            L.cmd("HALT_CLEAR")
    ctx.check(hb.check_all_eq("random STOP / HALT: MT-2 = |Δpos_steps|", sp_pairs,
                              "± 1 accepted only with POS_UNCERTAIN"))
    ctx.check(hb.info("E-stop stops", "see HG-10 a/b", note="counter vs Δpos is checked in every E-stop trial"))


# ---------------------------------------------------------------------------------------- §6.8 bench block
def bench_entry(ctx: Ctx) -> None:
    """§6.8 P-1…P-5: E-stop sense bypassed by J-STIM (HG-29 e E-stop part, HG-10 a)."""
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    if not ctx.twin:
        r = ctx.result_of("HG-10cd")
        if not r or r["verdict"] != hb.PASS:
            raise BenchRefused("§6.8 P-2: HG-10 c/d (D-42 hardwired ENA cut) has not passed in this session — "
                               "no E-stop sense bypass")
    else:
        ctx.note("P-2 gate (HG-10 c/d PASS) is enforced on the board; in the twin dry run HG-10 c/d is MANUAL")
    names = op.ask_text("P-1", "P-1: no specimen, no load, no tool in the travel; operator at the PC and observer at "
                        "the operator panel (red button + PSU switch in reach); both limit switches connected. "
                        "Names of operator / observer", nominal="(dry run)")
    ctx.data("persons", names.value)
    lo, hi = ctx.cfg["bench_window_um"]
    orig = {k: L.get(k) for k in ("limits.soft_min_um", "limits.soft_max_um")}
    ctx.shared["bench_orig"] = orig
    L.set_ok("limits.soft_min_um", lo)
    L.set_ok("limits.soft_max_um", hi)
    ctx.check(hb.info("P-1 soft-limit window", f"{lo / 1000:g}…{hi / 1000:g} mm", note="50 mm window next to START"))
    op.instruct("P-3", "P-3: driver PSU OFF; unplug the NC sense connector at the interface board; fit J-STIM "
                "(PB8, 220 Ω) to the PA10 input connector; hang 'E-STOP SENSE BYPASSED — TEST' on the red button; photo",
                twin=lambda tw: tw.act("drv_power", on=False))
    r = op.ask_number("P-4R", "P-4: J-STIM series resistor value", "ohm", nominal=220.0)
    ctx.judged(hb.check_range("P-4 J-STIM resistor", [r.value or 0], 200, 240, 2, "ohm"), r)
    v = op.ask_number("P-4V", "P-4: DMM PA10 pin (CN10-33) with STIM idle", "V", nominal=0.6)
    ctx.judged(hb.check_le("P-4 PA10 idle (valid low)", [v.value or 0], 0.99, ctx.cfg["dmm_u_v"], "V"), v)
    # P-5 static check with the driver unpowered
    clear_latches(ctx)
    st0 = L.status()
    M.probe_arm("ESTOP", "TRIGGER", False, 17)
    M.stim_run(1, 100, False, seed=1)
    L.wait(40, keepalive=False)
    st1 = L.status()
    L.wait(120, keepalive=False)
    ok = "ESTOP_OPEN" not in st0["io"] and "ESTOP_OPEN" in st1["io"]
    ctx.check(hb.check_true("P-5 ESTOP_OPEN follows STIM", ok, "0 idle, 1 during a STIM hold (driver unpowered)",
                            {"idle": st0["io"], "stim": st1["io"]}))
    if not ok and not ctx.twin:
        raise BenchRefused("P-5 failed: the stimulus does not drive the E-stop sense — stop, restore (P-8)")
    clear_latches(ctx)
    if not ctx.twin:
        rc_delay_estop(ctx)
    else:
        ctx.check(hb.target_only("HG-29 e E-stop input RC delay", "recorded (u_th E-stop)", "J-AUX not modelled"))
    op.instruct("P-6", "P-6: driver PSU ON; observer keeps a hand at the red button (D-42 still active) and the PSU switch",
                twin=lambda tw: tw.act("drv_power", on=True))
    L.wait(600)
    ctx.check(hb.info("P-7 abort criteria", "unexpected motion / noise / ALM / outside the window -> red button + PSU "
                      "off, operator types 'abort' (the session sends HALT)"))


def rc_delay_estop(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    op.instruct("RC-ESTOP", "HG-29 e: J-EVT <- PB8 (STIM), J-AUX <- PA10 node (J-STIM stays on the PA10 connector)")
    n_evt0, n_aux0 = M.stamp_count("EVT"), M.stamp_count("AUX")
    M.probe_arm("STIM", "TRIGGER", False, 1799)
    M.stim_run(10, 5, False, seed=7)
    L.wait(150, keepalive=False)
    evs = [v for v in M.stamp_entries("EVT", n_evt0, n_evt0 + 9).values() if v is not None]
    n = M.stamp_count("AUX") - n_aux0
    aux = [v for v in M.stamp_entries("AUX", n_aux0, n_aux0 + n - 1).values() if v is not None] if n else []
    d = [min([hb.sdiff32(a, e) for a in aux if 0 <= hb.sdiff32(a, e) < 500] or [999]) for e in evs]
    if d and max(d) < 999:
        ctx.shared.setdefault("u_th_measured", {})["ESTOP"] = max(d) + 1.0
        ctx.check(hb.info("HG-29 e E-stop RC delay", max(d), "µs", f"u_th ESTOP := {max(d) + 1:.1f} µs"))
    else:
        ctx.check(hb.check_true("HG-29 e E-stop RC delay", False, "AUX edge after each STIM edge", d))
    clear_latches(ctx)
    op.instruct("RC-ESTOP-2", "J-EVT <- PA10 node (for HG-10 a); J-ENA and J-PUL-A fitted")


def bench_exit(ctx: Ctx) -> None:
    """§6.8 P-8 restore — must PASS before any further motion."""
    L, op = ctx.L, ctx.op
    try:
        stop_motion(ctx)
    except Exception:  # noqa: BLE001
        L.cmd("HALT")
    op.instruct("P-8a", "P-8: driver PSU OFF; remove J-STIM; reconnect the NC sense connector; remove the tag; photo",
                twin=lambda tw: tw.act("drv_power", on=False))
    op.instruct("P-8b", "P-8: driver PSU ON", twin=lambda tw: tw.act("drv_power", on=True))
    L.wait(600)
    clear_latches(ctx)
    enable(ctx)
    n0 = L.n_events()
    op.instruct("P-8c", "P-8: press the red E-stop (MCU running)",
                twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False))
    L.wait(100)
    st = L.status()
    ev = [e["code"] for e in L.events_since(n0)]
    ok = "ESTOP_SET" in ev and "ESTOP" in st["flags"] and "ENA_DISABLED" in st["io"] and "ESTOP_OPEN" in st["io"]
    ctx.check(hb.check_true("P-8 sense restored: press", ok, "EVENT ESTOP_SET, ENA disabled, ESTOP latched, ESTOP_OPEN = 1",
                            {"events": ev, "io": st["io"]}))
    release_estop_input(ctx)
    L.wait(int(L.get("io.estop_release_ms")) + 50)
    r = L.cmd("ESTOP_CLEAR")
    st = L.status()
    ok2 = r["status"] == "OK" and "ESTOP_OPEN" not in st["io"] and "ESTOP" not in st["flags"]
    ctx.check(hb.check_true("P-8 sense restored: release + ESTOP_CLEAR", ok2, "ESTOP_OPEN follows the button", st["io"]))
    for k, v in ctx.shared.get("bench_orig", {}).items():
        L.set_ok(k, v)
    ctx.check(hb.info("soft limits restored", ctx.shared.get("bench_orig", {})))


# ---------------------------------------------------------------------------------------- HG-10 a / b / e
def estop_trial(ctx: Ctx, real: bool, i: int) -> dict:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    lo, hi = ctx.cfg["bench_window_um"]
    st = clear_latches(ctx)
    ready(ctx, lo + 2_000, 30_000)
    psc = 17
    tick = hb.probe_tick_us(psc)
    M.counter(reset=True)
    s0 = L.status()["pos_steps"]
    n_evt0, n_pul0 = M.stamp_count("EVT"), M.stamp_count("PUL")
    M.probe_arm("ESTOP", "TRIGGER", False, psc)
    v = ctx.cfg["trial_v_um_s"]
    n0 = L.n_events()
    jog(ctx, v)
    L.jog_hold(v, ctx.rng.uniform(350, 650))
    if real:
        op.cue("PRESS", "PRESS the red E-stop NOW (table moving)",
               twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False, bounce_ms=[0.3, 0.2, 0.5]),
               delay_ms=(20, 200))
        ev = L.wait_event("ESTOP_SET", n0, 3000, jog_v=v)
    else:
        M.stim_run(1, 20, False, seed=ctx.rng.randrange(1, 2**31))
        ev = L.wait_event("ESTOP_SET", n0, 200, keepalive=False)
        L.wait(30, keepalive=False)
    pr = M.probe_read()
    c, _ = M.counter()
    st = L.status()
    t_evt = stamp(ctx, "EVT", n_evt0)
    puls = M.newest("PUL", 4)
    phase = None
    period_us = 1e6 / (v * spm(ctx) / 1000)
    if t_evt is not None and puls:
        before = [p for p in puls if hb.sdiff32(t_evt, p) >= 0]
        if before:
            phase = hb.sdiff32(t_evt, before[0]) / period_us
    after = any(hb.sdiff32(p, t_evt) > 0 for p in puls) if (t_evt is not None and puls) else None
    last_pul = pr["ccr2"] * tick if pr["ccr2"] else 0.0
    ena = pr["ccr3"] * tick if pr["ccr3"] else None
    if ena is None and ctx.twin and "ENA_DISABLED" in st["io"]:
        ena = 0.0       # twin: the FW reaction runs at the event instant (zero-latency model, OBS-E-HG-03)
    tr = {"i": i, "real": real, "event": bool(ev), "flags": pr["flags"], "last_pul_us": last_pul, "ena_us": ena,
          "pul_after_event": after, "phase": phase, "counter": c, "dpos": abs(st["pos_steps"] - s0),
          "pos_uncertain": "POS_UNCERTAIN" in st["status"],
          "estop_latched": "ESTOP" in st["flags"], "ena_disabled": "ENA_DISABLED" in st["io"],
          "homed_cleared": "HOMED" not in st["flags"]}
    if real:
        release_estop_input(ctx)
    return tr


def eval_estop(ctx: Ctx, trials: list[dict], tag: str) -> None:
    tick = hb.probe_tick_us(17)
    u = tick + ctx.u_th("ESTOP")
    ok_tr = [t for t in trials if t["event"] and "TRIGGERED" in t["flags"]]
    ctx.check(hb.check_eq(f"{tag} trials with E-stop event + probe trigger", len(ok_tr), len(trials)))
    ctx.check(hb.check_le(f"{tag} last PUL after the E-stop edge", [t["last_pul_us"] for t in ok_tr], 100.0, u, "µs",
                          note=f"MT-3 PSC 17 (0.1 µs) + u_th {ctx.u_th('ESTOP'):.1f} µs (R-3)"))
    enas = [t["ena_us"] for t in ok_tr if t["ena_us"] is not None]
    if len(enas) < len(ok_tr):
        ctx.note(f"{tag}: {len(ok_tr) - len(enas)} trials without an ENA capture")
    ctx.check(hb.check_le(f"{tag} ENA disabled after the E-stop edge", enas, 1000.0, u, "µs",
                          note="twin: zero-latency reaction model, CCR3 = 0 taken as 0 µs (OBS-E-HG-03)" if ctx.twin else ""))
    ctx.check(hb.check_true(f"{tag} CCR2 consistent with PUL stamps", all((t["last_pul_us"] > 0) == bool(t["pul_after_event"])
                                                                          for t in ok_tr if t["pul_after_event"] is not None),
                            "CCR2 = 0 <=> no PUL stamp after the event"))
    pairs = [(t["counter"], t["dpos"] if not (t["pos_uncertain"] and abs(t["counter"] - t["dpos"]) <= 1) else t["counter"])
             for t in trials]
    ctx.check(hb.check_all_eq(f"{tag} MT-2 = |Δpos_steps| (HG-09 E-stop part)", pairs,
                              "± 1 accepted only with POS_UNCERTAIN (truncated pulse, ICD §6.2 v0.7.2); "
                              f"{sum(1 for t in trials if t['pos_uncertain'])} trials with POS_UNCERTAIN"))
    ctx.check(hb.check_true(f"{tag} latched, ENA disabled, HOMED cleared",
                            all(t["estop_latched"] and t["ena_disabled"] and t["homed_cleared"] for t in ok_tr),
                            "SAF-FW-005 state after every trial"))
    ph = [t["phase"] for t in ok_tr if t["phase"] is not None]
    if ph:
        bins = [0] * 10
        for p in ph:
            bins[min(9, max(0, int(p * 10)))] += 1
        ctx.check(hb.info(f"{tag} event phase vs step period (10 bins)", bins, note="§6.1 rule 3 phase coverage"))
    ctx.data(tag, trials)


def hg10a(ctx: Ctx) -> None:
    trials = [estop_trial(ctx, False, i) for i in range(ctx.cfg["n_estop_stim"])]
    eval_estop(ctx, trials, "a STIM")


def hg10b(ctx: Ctx) -> None:
    ctx.jumpers("J-EVT <- PA10 node (real button contact), J-ENA, J-PUL-A")
    trials = [estop_trial(ctx, True, i) for i in range(ctx.cfg["n_estop_press"])]
    eval_estop(ctx, trials, "b real press")
    # (e) slow press, FW with the ENA forced externally (D-42)
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    for k in range(2):
        clear_latches(ctx)
        enable(ctx)
        M.counter(reset=True)
        n0 = L.n_events()
        op.instruct(f"e{k}", "Press the red E-stop SLOWLY (≈ 1 s half-way, then fully) so that the NO contact (D-42) acts "
                    + ("BEFORE" if k == 0 else "AFTER (press quickly past the first point)") + " the NC sense opens; hold",
                    twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False, bounce_ms=[2, 1, 3]))
        L.wait(300)
        st = L.status()
        evs = L.events_since(n0)
        codes = [e["code"] for e in evs]
        ok = "ESTOP" in st["flags"] and not st["faults"] and "FAULT_SET" not in codes and "ENA_DISABLED" in st["io"]
        ctx.check(hb.check_true(f"e{k} no unexpected FW state", ok, "only ESTOP latched, no fault / extra latch", codes))
        release_estop_input(ctx)
        L.wait(500)
        st = L.status()
        c, _ = M.counter()
        ctx.check(hb.check_true(f"e{k} release never causes motion", c == 0 and "ESTOP" in st["flags"] and
                                st["motion_state"] == "NOT_ENABLED", "counter 0, ESTOP latched, NOT_ENABLED",
                                {"counter": c, "state": st["motion_state"]}))
        L.wait(int(L.get("io.estop_release_ms")) + 30)
        L.ok("ESTOP_CLEAR")
        enable(ctx)
        r = L.cmd("MOVE_ABS", {"target_um": 10_000, "v_um_s": 5_000, "a_um_s2": 0})
        ctx.check(hb.check_true(f"e{k} HOME required", r["status"] == "E_STATE" and "NOT_HOMED" in block_bits(r.get("detail", 0)),
                                "MOVE_ABS refused NOT_HOMED", r))
    if ctx.twin:
        ctx.note("e: the twin has no model of the D-42 NO contact forcing ENA — the FW-state part ran with a normal press")


# ---------------------------------------------------------------------------------------- HG-11
def hg11(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    v = ctx.cfg["trial_v_um_s"]
    for lim, src, sign, x_start in (("START", "LIMIT_START", -1, 45_000), ("END", "LIMIT_END", +1, 15_000)):
        op.instruct(f"{lim}-STIM", f"Unplug the {lim} switch; J-STIM (220 Ω) -> its input connector; J-EVT <- {lim} pin "
                    f"node; J-PUL-A fitted (§6.8 steps without P-2; the red button stays fully operational)")
        psc = 1
        tick = hb.probe_tick_us(psc)
        trials = []
        for i in range(ctx.cfg["n_limit_stim"]):
            ready(ctx, x_start, v)
            M.counter(reset=True)
            s0 = L.status()["pos_steps"]
            M.probe_arm(src, "TRIGGER", False, psc)
            n0 = L.n_events()
            jog(ctx, sign * v)
            L.jog_hold(sign * v, ctx.rng.uniform(350, 650))
            M.stim_run(1, 20, False, seed=ctx.rng.randrange(1, 2**31))
            ev = L.wait_event("LIMIT_SET", n0, 200, keepalive=False)
            L.wait(30, keepalive=False)
            pr = M.probe_read()
            c, _ = M.counter()
            st = L.status()
            wait_done(ctx, n0, 2000)
            trials.append({"event": bool(ev), "flags": pr["flags"], "last_pul_us": pr["ccr2"] * tick,
                           "counter": c, "dpos": abs(st["pos_steps"] - s0)})
            L.wait(int(L.get("io.release_ms")) + 30)
        okt = [t for t in trials if t["event"] and "TRIGGERED" in t["flags"]]
        u = tick + ctx.u_th(src)
        ctx.check(hb.check_eq(f"{lim} STIM trials with LIMIT_SET + trigger", len(okt), len(trials)))
        ctx.check(hb.check_le(f"{lim} STIM: last PUL after the limit edge", [t["last_pul_us"] for t in okt], 200.0, u, "µs",
                              note=f"MT-3 PSC 1 + u_th {ctx.u_th(src):.1f} µs (R-3)"))
        ctx.check(hb.check_all_eq(f"{lim} STIM: MT-2 = |Δpos_steps|", [(t["counter"], t["dpos"]) for t in trials]))
        ctx.data(f"{lim}_stim", trials)
        op.instruct(f"{lim}-REAL", f"Remove J-STIM; reconnect the {lim} switch (J-EVT stays on its pin node)")
        # real actuations at ≤ 1 mm/s: soft limit moved beyond the switch for this part only
        key, far = ("limits.soft_min_um", -10_000) if lim == "START" else ("limits.soft_max_um", 400_000)
        psc_r = 17
        tick_r = hb.probe_tick_us(psc_r)
        real = []
        orig_max = int(L.get("limits.soft_max_um"))
        for i in range(ctx.cfg["n_limit_real"]):
            ready(ctx, 3_000 if lim == "START" else orig_max - 1_000, 20_000)
            with params(ctx, {key: far}):
                M.probe_arm(src, "TRIGGER", False, psc_r)
                n0 = L.n_events()
                jog(ctx, sign * 1000)
                ev = L.wait_event("LIMIT_SET", n0, 40_000, jog_v=sign * 1000)
                wait_done(ctx, n0, 3000)
                pr = M.probe_read()
                real.append({"event": bool(ev), "flags": pr["flags"], "last_pul_us": pr["ccr2"] * tick_r})
                r = L.cmd("JOG", {"v_um_s": sign * 1000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
                ctx.check(hb.check_true(f"{lim} latch refuses motion toward the switch", r["status"] == "E_STATE",
                                        "E_STATE LIMIT", r)) if i == 0 else None
                if r["status"] == "OK":
                    jog_stop(ctx)
                back = 3_000 if lim == "START" else orig_max - 1_000
                move_abs(ctx, back, 2_000)
                L.wait(int(L.get("io.release_ms")) + 30)
        okr = [t for t in real if t["event"] and "TRIGGERED" in t["flags"]]
        ctx.check(hb.check_eq(f"{lim} real actuations detected", len(okr), len(real)))
        ctx.check(hb.check_le(f"{lim} real: last PUL after the switch edge", [t["last_pul_us"] for t in okr], 200.0,
                              tick_r + ctx.u_th(src), "µs", note="1 mm/s, real contact incl. bounce"))
        ctx.data(f"{lim}_real", real)


# ---------------------------------------------------------------------------------------- HG-13
def hg13(ctx: Ctx) -> None:
    L, M = ctx.L, ctx.L.meas
    ctx.jumpers("J-EVT <- PA3 (USART2 RX), J-PUL-A fitted")
    psc = 17
    tick = hb.probe_tick_us(psc)
    v = ctx.cfg["trial_v_um_s"]
    res: dict[str, list[float]] = {"STOP": [], "HALT": []}
    bad = []
    if ctx.twin:
        baud = 921_600.0
        byte_us = hb.uart_frame_us(1, baud)
    else:
        baud = ctx.cfg["baud_actual"]
    for kind in ("STOP", "HALT"):
        n_bytes = 9 if kind == "STOP" else 8
        for i in range(ctx.cfg["n_pc_stop"]):
            st = clear_latches(ctx)
            ready(ctx, 10_000, 30_000)
            n0 = L.n_events()
            jog(ctx, v)
            L.jog_hold(v, 400)
            M.probe_arm("RX", "TRIGGER", True, psc)
            L.advance(ctx.rng.uniform(5.0, 100.0))          # ≥ 5 ms line silence (dead-man 250 ms not reached)
            if kind == "STOP":
                L.send("STOP", {"mode": 0})
            else:
                L.send("HALT")
            wait_done(ctx, n0, 3000)
            pr = M.probe_read()
            if "TRIGGERED" not in pr["flags"] or "WINDOW_OVERFLOW" in pr["flags"]:
                bad.append((kind, i, pr["flags"]))
                continue
            # target: the trigger is the start bit of byte 0; the twin model triggers at the END of byte 0
            ref = (n_bytes - 1) * byte_us if ctx.twin else hb.uart_frame_us(n_bytes, baud)
            res[kind].append(max(pr["ccr2"] * tick - ref, 0.0) if pr["ccr2"] else 0.0)
            if kind == "HALT":
                L.cmd("HALT_CLEAR")
    u = tick + 1.1
    for kind, vals in res.items():
        ctx.check(hb.check_le(f"{kind}: last byte end -> last PUL", vals, 2000.0, u, "µs",
                              note=f"MT-3 PSC 17 on RX, frame end computed at {baud:.0f} Bd"))
    ctx.check(hb.check_eq("trials with a valid RX trigger", sum(len(v) for v in res.values()), 2 * ctx.cfg["n_pc_stop"],
                          note=str(bad[:3])))
    if ctx.twin:
        ctx.note("twin DIAG_MEAS model triggers the RX probe at the end of each received byte (start bit on the board); "
                 "the evaluation uses the matching reference point (OBS-E-HG-01)")
    ctx.check(hb.na("Pause/Break key -> last edge (NFR-003)", "≤ 100 ms p95", "M3 (SW hotkey on the reference PC)"))


# ---------------------------------------------------------------------------------------- HG-17
def hg17(ctx: Ctx) -> None:
    L, op = ctx.L, ctx.op
    for inp, sign, cause, flag in (("estop", +1, "ESTOP", "ESTOP"), ("start", -1, "LIMIT_START", "LIMIT_START"),
                                   ("end", +1, "LIMIT_END", "LIMIT_END")):
        ready(ctx, 50_000 if inp != "end" else 240_000)
        n0 = L.n_events()
        jog(ctx, sign * 2000)
        L.jog_hold(sign * 2000, 200)
        name = {"estop": "E-stop NC sense", "start": "START switch", "end": "END switch"}[inp]
        op.cue(f"BREAK-{inp}", f"Unplug the {name} connector NOW (table jogging slowly)",
               twin=lambda tw, n=inp: tw.act("wire", input=n, broken=True), delay_ms=(20, 100))
        ev = L.wait_event("STOPPED", n0, 5000, jog_v=sign * 2000)
        st = L.status()
        ok = ev is not None and ev["arg"] == STOP_CAUSE[cause] and has(st, flag)
        ctx.check(hb.check_true(f"{name} wire break -> stop + flag", ok, f"STOPPED({cause}) and {flag} reported",
                                {"stopped": ev, "flags": st["flags"], "status": st["status"]}))
        op.instruct(f"FIX-{inp}", f"Reconnect the {name} connector",
                    twin=lambda tw, n=inp: tw.act("wire", input=n, broken=False))
        L.wait(300)
        clear_latches(ctx)
    a = op.ask_yes_no("NO-BREAK", "D-42 NO wire disconnected, MCU held in reset, E-stop pressed: is the shaft still "
                      "HOLDING (= the break is NOT detected; residual R-10)?", nominal=True)
    ctx.judged(hb.check_true("D-42 NO wire break recorded as undetectable", a.value, "R-10, mitigated by HG-10 c periodic"), a)
    op.instruct("NO-FIX", "Reconnect the D-42 NO wire; release the MCU reset and the E-stop; repeat HG-10 c1 once")


# ---------------------------------------------------------------------------------------- HG-04
def hg04(ctx: Ctx) -> None:
    """20+ SAVEs; the NVM log (FW_design §5.11: 32 slots per 16 KB sector, two sectors) erases a sector only when the
    active one is full, i.e. every 32nd SAVE once both sectors were used. The first erase is found by saving until
    one is observed, then the E-stop presses are placed on the predicted erase SAVEs (OI-E-HG-04: a J-STIM pulse
    cannot be placed into the erase — DIAG_MEAS received during a SAVE executes after the flash op, D-37 a)."""
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    ctx.jumpers("J-EVT <- PA10 node (sense connected, real button), J-ENA" + ("" if ctx.twin else "; J-AUX <- PA2 (TX)"))
    key = "stream.fallback_hz"
    orig = int(L.get(key))
    clear_latches(ctx)
    enable(ctx)
    L.ok("STREAM_START")
    boots0 = len([e for e in L.events() if e["code"] == "BOOT"])
    st0 = L.status()
    psc = 17
    tick = hb.probe_tick_us(psc)
    want_hits = 1 if ctx.cfg.get("quick") else 3
    max_saves = max(ctx.cfg["n_save"], 32 * (want_hits + 2) + 8)
    trials: list[dict] = []
    last_erase = None
    i = 0
    while i < max_saves:
        hits = [t for t in trials if t.get("in_window") and t["erase"]]
        if i >= ctx.cfg["n_save"] and len(hits) >= want_hits:
            break
        if i:
            clear_latches(ctx)
        enable(ctx)
        val = (orig % 80) + 1 if i % 2 == 0 else orig
        L.set_ok(key, val)
        predicted = last_erase is not None and (i - last_erase) % 32 == 0
        press = predicted or i < 2                         # 2 presses during plain (program-only) SAVEs too
        n_evt0 = M.stamp_count("EVT")
        M.probe_arm("ESTOP", "TRIGGER", False, psc)
        er_before = ctx.tw.act("query", what="flash")["erases"] if ctx.twin else None
        if press and not ctx.twin:
            op.instruct("READY", "Hand on the red E-stop. After Enter the session prints GO and starts a SAVE that "
                        "includes a sector erase (≈ 0.3…0.5 s): press as soon as you see GO")
            L.advance(ctx.rng.uniform(0, 150))
        n0 = L.n_events()
        t0 = L.now_ns()
        L.send("SAVE_PARAMS")
        if press:
            op.cue("PRESS", "GO — press the red E-stop NOW (SAVE running)",
                   twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False), delay_ms=(1.5, 12.0))
        ev = L.wait_event("PARAMS_SAVED", n0, 5000, keepalive=False, step_ms=0.5)
        rtt = (L.now_ns() - t0) / 1e6
        L.wait(30)
        st = L.status()
        er_after = ctx.tw.act("query", what="flash")["erases"] if ctx.twin else None
        erase = (er_after - er_before) > 0 if ctx.twin else st["nvm_save_ms"] >= ctx.cfg["erase_threshold_ms"]
        if erase:
            last_erase = i
        tr = {"i": i, "val": val, "press": press, "predicted": predicted, "save_ms": st["nvm_save_ms"], "rtt_ms": rtt,
              "erase": erase, "saved": ev is not None}
        if press:
            es = L.wait_event("ESTOP_SET", n0, 3000)
            pr = M.probe_read()
            t_evt = stamp(ctx, "EVT", n_evt0)
            hit = False
            if ev is not None and t_evt is not None:
                hit = 0 <= hb.sdiff32(ev["t_us"], t_evt) <= st["nvm_save_ms"] * 1000
            ena = pr["ccr3"] * tick if pr["ccr3"] else (0.0 if ctx.twin and "ENA_DISABLED" in L.status()["io"] else None)
            tr.update({"estop": es is not None, "ena_us": ena, "in_window": hit,
                       "flags": pr["flags"]})
            release_estop_input(ctx)
            L.wait(int(L.get("io.estop_release_ms")) + 30)
        trials.append(tr)
        i += 1
    clear_latches(ctx)
    L.set_ok(key, orig)
    L.ok("SAVE_PARAMS", timeout_ms=5000)                    # the stored value is the original again
    boots = len([e for e in L.events() if e["code"] == "BOOT"]) - boots0
    st1 = L.status()
    ctx.data("trials", trials)
    ctx.check(hb.check_true("no IWDG reset during the SAVEs", boots == 0 and st1["reset_cause"] == st0["reset_cause"] and
                            st1["uptime_ms"] > st0["uptime_ms"], "no BOOT event, uptime continuous", boots))
    ctx.check(hb.check_le("SAVE duration (nvm_save_ms)", [t["save_ms"] for t in trials], 2500, 1, "ms",
                          note=f"{len(trials) + 1} SAVEs"))
    ctx.check(hb.check_le("SAVE response (PC, upper bound)", [t["rtt_ms"] for t in trials], 2500 + 50, 0, "ms"))
    n_er = sum(1 for t in trials if t["erase"])
    ctx.check(hb.check_ge("SAVEs that included a sector erase", [n_er], 1, 0, "SAVEs",
                          note="twin flash log" if ctx.twin else f"nvm_save_ms ≥ {ctx.cfg['erase_threshold_ms']} ms"))
    prog = [t for t in trials if t.get("press") and t.get("estop") and not t["erase"] and t.get("ena_us") is not None]
    if prog:
        ctx.check(hb.check_le("a ENA disabled ≤ 1 ms with the E-stop during a SAVE (program)", [t["ena_us"] for t in prog],
                              1000.0, tick + ctx.u_th("ESTOP"), "µs"))
    hits = [t for t in trials if t.get("press") and t.get("in_window") and t["erase"]]
    enas = [t["ena_us"] for t in hits if t.get("ena_us") is not None]
    if hits:
        ctx.check(hb.check_le("a ENA disabled ≤ 1 ms with the E-stop inside an erase", enas, 1000.0,
                              tick + ctx.u_th("ESTOP"), "µs", note=f"{len(hits)} presses inside an erase window"))
    else:
        ctx.check(hb.not_measured("a ENA disabled ≤ 1 ms with the E-stop inside an erase", "≥ 1 press inside an erase",
                                  "no press landed inside an erase window — repeat"))
    ctx.check(hb.na("b DRV_PWR change during the erase", "erase time + 25 ms", "optional: no presence sense (D-41)")
              if not ctx.cfg["power_sense_fitted"] else hb.manual("b DRV_PWR during erase", "see HG-21 procedure"))
    if ctx.twin:
        ctx.check(hb.target_only("c no gap > 22 µs inside a TX frame (J-AUX = TX)", "MT-4 stamps of every TX edge",
                                 "J-AUX not modelled in the twin"))
    else:
        tx_gaps(ctx)
    # record valid after reboot
    L.ok("REBOOT", {"magic": rc.REBOOT_MAGIC})
    L.wait(300, keepalive=False)
    L.cmd("PING")
    st2 = L.status()
    back = int(L.get(key))
    ctx.check(hb.check_true("valid record after reboot", back == orig and "NVM_DEFAULTED" not in st2["sys_flags"],
                            f"{key} == last saved, no NVM_DEFAULTED", {"read": back, "sys": st2["sys_flags"]}))


def tx_gaps(ctx: Ctx) -> None:
    """HG-04 c (target): J-AUX = TX — bursts separated by > 22 µs; a burst shorter than the shortest frame
    (8 bytes ≈ 87 µs) indicates a split frame. Heuristic; reviewed with the PC frame counters."""
    M = ctx.L.meas
    n = M.stamp_count("AUX")
    st = M.stamp_entries("AUX", max(0, n - 2000), n - 1)
    ts = [v for k, v in sorted(st.items()) if v is not None]
    bursts, cur = [], [ts[0]] if ts else []
    for a, b in zip(ts, ts[1:]):
        if hb.sdiff32(b, a) > 22:
            bursts.append(cur)
            cur = [b]
        else:
            cur.append(b)
    if cur:
        bursts.append(cur)
    short = [hb.sdiff32(bb[-1], bb[0]) for bb in bursts if hb.sdiff32(bb[-1], bb[0]) < 80]
    ctx.check(hb.check_eq("c TX bursts shorter than one frame", len(short), 0, f"{len(bursts)} bursts analysed"))


# ---------------------------------------------------------------------------------------- HG-14
def hg14(ctx: Ctx) -> None:
    L, M = ctx.L, ctx.L.meas
    pul_ms, rst_ms, causes, prev = [], [], [], []
    for where in ("MAIN", "TICK", "ISR1"):
        for i in range(ctx.cfg["n_hang"]):
            ready(ctx, 20_000)
            M.noinit(clear=True)
            jog(ctx, 10_000)
            L.jog_hold(10_000, 300)
            r = M.hang(where, 0)
            if r["status"] != "OK":
                raise LinkError(f"HANG {where}: {r}")
            L.advance(400.0)                                       # FW hangs -> IWDG reset; no frames
            L.wait(100, keepalive=False)
            st = L.status()
            ni = M.noinit()
            causes.append((where, st["reset_cause"]))
            prev.append(ni["prev_valid"])
            if ni["prev_valid"]:
                pul_ms.append(hb.sdiff32(ni["prev_pul"], ni["prev_hang"]) / 1000)
                rst_ms.append(hb.sdiff32(ni["prev_hb"], ni["prev_hang"]) / 1000)
            ctx.data(f"{where}_{i}", ni)
    ctx.check(hb.check_true("reset cause IWDG", all(c == "IWDG" for _, c in causes), "every trial", causes[:6]))
    ctx.check(hb.check_true(".noinit record valid after the reset", all(prev), "w4 prev_valid = 1"))
    ctx.check(hb.check_le("last PUL − hang start", pul_ms, 100.0, 0.1, "ms", note="SAF-FW-019"))
    ctx.check(hb.check_le("heartbeat end − hang start (IWDG time)", rst_ms, 90.0, 0.1, "ms", note="plan: ≤ 90 ms"))


# ---------------------------------------------------------------------------------------- HG-16 / HG-24
def hg16(ctx: Ctx) -> None:
    L, op = ctx.L, ctx.op
    clear_latches(ctx)
    enable(ctx)
    n0 = L.n_events()
    st_on = L.status()
    op.instruct("PSU-OFF", "Switch the 48 V driver PSU OFF (driver unpowered)", twin=lambda tw: tw.act("drv_power", on=False))
    L.wait(300)
    st_off = L.status()
    alm_off = "ALM" in st_off["io"]
    ctx.check(hb.check_true("ALM active with the driver unpowered", alm_off, "fail-safe reading (wiring §3)",
                            {"on": st_on["io"], "off": st_off["io"]}))
    r = jog(ctx, 1000)
    ctx.check(hb.check_true("new motion refused while ALM active (DRV_PWR assumed present)",
                            r["status"] == "E_STATE" and "DRIVER_ALARM" in block_bits(r.get("detail", 0)),
                            "E_STATE BLOCK DRIVER_ALARM (SAF-FW-026)", r))
    if r["status"] == "OK":
        jog_stop(ctx)
    for k, q, nom in (("ALM-V-off", "DMM at the ALM input pin PA8, driver unpowered", 3.3),
                      ("PEND-V-off", "DMM at the PEND input pin PA9, driver unpowered", 3.3)):
        a = op.ask_number(k, q, "V", nominal=nom)
        ctx.judged(hb.info(k, a.value, "V"), a) if a.evidence else ctx.check(hb.manual(k, "recorded", a.value, a.source))
    op.instruct("PSU-ON", "Switch the 48 V driver PSU ON; wait 2 s", twin=lambda tw: tw.act("drv_power", on=True))
    L.wait(800)
    st = L.status()
    alm_ev = [e["arg"] for e in L.events_since(n0, "ALM_CHANGED")]
    ctx.check(hb.check_true("ALM inactive when powered", "ALM" not in st["io"], "ALM low impedance = OK", st["io"]))
    ctx.check(hb.check_eq("one ALM_CHANGED per change", alm_ev, [1, 0]))
    for k, q, nom in (("ALM-V-on", "DMM at PA8, driver powered, idle", 0.1),
                      ("PEND-V-on", "DMM at PA9, driver powered, in position", 3.3)):
        a = op.ask_number(k, q, "V", nominal=nom)
        ctx.check(hb.info(k, a.value, "V")) if a.evidence else ctx.check(hb.manual(k, "recorded", a.value, a.source))
    # PEND while moving vs idle (closed-loop setting)
    ready(ctx, 50_000)
    if ctx.twin:
        ctx.tw.act("driver", pend_auto=True)
    L.ok("MOVE_ABS", {"target_um": 150_000, "v_um_s": 20_000, "a_um_s2": 0})
    n1 = L.n_events()
    L.wait(500)
    pend_mov = "PEND" in L.status()["io"]
    wait_done(ctx, n1 - 0, 30_000)
    L.wait(300)
    pend_idle = "PEND" in L.status()["io"]
    ctx.check(hb.info("PEND moving / in position", f"{pend_mov} / {pend_idle}",
                      note="levels recorded; drv.pend_active_level set accordingly (closed loop)"))
    if ctx.twin:
        n2 = L.n_events()
        L.ok("MOVE_ABS", {"target_um": 50_000, "v_um_s": 20_000, "a_um_s2": 0})
        L.wait(300)
        ctx.tw.act("alm", active=True)
        md = wait_done(ctx, n2, 30_000)
        ctx.tw.act("alm", active=False)
        ctx.check(hb.check_true("running move unaffected by ALM", md is not None and md["arg"] == MD["TARGET"],
                                "MOVE_DONE TARGET", md))
    else:
        ctx.check(hb.na("running move unaffected by ALM", "MOVE_DONE TARGET",
                        "no safe ALM source while moving on the bench; covered by TC-SAF-FW-026 on the twin"))
    a = op.ask_text("LEVELS", "Resulting drv.alm_active_level / drv.pend_active_level (keep / change)", nominal="keep")
    ctx.check(hb.manual("drv.*_active_level set", "recorded", a.value, a.source))


def hg24(ctx: Ctx) -> None:
    L, op = ctx.L, ctx.op
    a = op.ask_text("PROVOKE", "Provoke a driver ALM on the bench (method used, e.g. motor phase connector unplugged "
                    "with PSU off before, or under-voltage)", nominal="(dry run)")
    L.wait(500)
    st = L.status()
    ctx.check(hb.manual("ALM provoked", "ALM active in GET_STATUS io", {"method": a.value, "io": st["io"]}, a.source)
              if not a.evidence else hb.check_true("ALM provoked", "ALM" in st["io"], "ALM active", st["io"]))
    op.instruct("PWRCYCLE", "Reset the alarm: 48 V PSU OFF, wait 5 s, ON (no RESET button since D-41)")
    L.wait(1000)
    st = L.status()
    b = op.ask_yes_no("PWRRES", "Driver alarm LED off after the power cycle?", nominal=True)
    ctx.judged(hb.check_true("power-cycle reset works", b.value and "ALM" not in st["io"], "ALM inactive", st["io"]), b)
    c = op.ask_text("ENATOG", "Provoke the ALM again; DISABLE / ENABLE (ENA toggle): result (cleared / not cleared)",
                    nominal="(dry run)")
    ctx.check(hb.manual("ENA-toggle reset", "recorded", c.value, c.source))


# ---------------------------------------------------------------------------------------- HG-05
def hg05(ctx: Ctx) -> None:
    L, M = ctx.L, ctx.L.meas
    ctx.jumpers("J-EVT <- PB4 (HX711 DOUT)" + ("" if ctx.twin else ", J-AUX <- PB10 (PD_SCK)"))
    dwt_ok = "DWT" in ctx.shared.get("meas_info", {}).get("variant", [])
    if dwt_ok:
        for sec in (19, 20):
            M.dwt(sec, reset=True)
    st0 = L.status()
    L.ok("STREAM_START")
    M.probe_arm("DOUT", "RESET", True, 17)
    n_evt = M.stamp_count("EVT")
    k0 = len(L.frames)
    dout: list[int] = []
    n_reads = ctx.cfg["hx_reads"]
    t_end = L.now_ns() + int(n_reads / 80.0 * 1e9)
    while L.now_ns() < t_end:
        L.wait(min(5000.0, (t_end - L.now_ns()) / 1e6 + 1))
        n1 = M.stamp_count("EVT")
        if n1 > n_evt:
            got = M.stamp_entries("EVT", n_evt, n1 - 1)
            dout += [got[k] for k in sorted(got) if got[k] is not None]
            if any(v is None for v in got.values()):
                ctx.note("EVT ring overrun between reads")
            n_evt = n1
    data = L.data_since(k0)
    st1 = L.status()
    ts = [d["t_us"] for d in data if d["afe_raw"] != rc.AFE_NO_DATA]
    per = [hb.sdiff32(b, a) for a, b in zip(dout, dout[1:])]
    med = statistics.median(per) if per else 0
    rate = 1e6 / med if med else 0
    lat, j = [], 0
    for t in ts:
        while j + 1 < len(dout) and hb.sdiff32(t, dout[j + 1]) >= 0:
            j += 1
        if dout and 0 <= hb.sdiff32(t, dout[j]) < 12_500:
            lat.append(hb.sdiff32(t, dout[j]))
    ctx.data("summary", {"samples": len(ts), "dout_stamps": len(dout), "median_period_us": med,
                         "rate_sps": rate, "status_rate_dsps": st1["afe_rate_dsps"]})
    ctx.check(hb.check_ge("samples", [len(ts)], 0.98 * n_reads, 0, "reads", note=f"{n_reads} requested"))
    ctx.check(hb.check_le("DATA t_us − DOUT edge (timestamp latency)", lat, 5.0, 1.0, "µs",
                          note="max ≤ 4 µs PASS; 4…6 µs -> HW_MEAS_DWT fine method"))
    if rate:
        ctx.check(hb.check_abs_le("reported rate vs MT-4 median", [(st1["afe_rate_dsps"] / 10 - rate) / rate * 100], 1.0,
                                  0.0, "%"))
    ctx.check(hb.check_eq("afe_reinit_count unchanged", st1["afe_reinit_count"] - st0["afe_reinit_count"], 0))
    if dwt_ok:
        ov = hb.dwt_overhead(hb.DwtSection.from_words(21, M.dwt(21)))
        ctx.check(hb.check_dwt("SCK-high (DWT section 19)", hb.DwtSection.from_words(19, M.dwt(19)), 50.0, ov))
        ctx.check(hb.check_dwt("HX711 read (DWT section 20)", hb.DwtSection.from_words(20, M.dwt(20)), 60.0, ov))
    else:
        ctx.check(hb.not_measured("SCK-high / read time (MT-1 DWT)", "≤ 50 µs / ≤ 60 µs",
                                  "needs the HW_MEAS_DWT image" + (" (DWT not modelled in the twin)" if ctx.twin else "")))
    if ctx.twin:
        ctx.check(hb.target_only("SCK edges per read 25 / 27 / 26 (J-AUX = PD_SCK)", "per gain, 100 reads",
                                 "J-AUX not modelled in the twin"))
    else:
        sck_pulses(ctx)


def sck_pulses(ctx: Ctx) -> None:
    L, M = ctx.L, ctx.L.meas
    want = {0: 25, 1: 27, 2: 26}                   # afe.gain_channel A128 / A64 / B32 -> SCK pulses per read (HX711 DS)
    orig = int(L.get("afe.gain_channel"))
    for g, pulses in want.items():
        L.set_ok("afe.gain_channel", g)
        L.wait(500)
        n0 = M.stamp_count("AUX")
        L.wait(100 / 80 * 1000)
        n1 = M.stamp_count("AUX")
        ts = [v for _, v in sorted(M.stamp_entries("AUX", max(n0, n1 - 2000), n1 - 1).items()) if v is not None]
        reads, cur = [], 1
        for a, b in zip(ts, ts[1:]):
            if hb.sdiff32(b, a) > 1000:
                reads.append(cur)
                cur = 1
            else:
                cur += 1
        counts = [r // 2 for r in reads if r > 4]
        ctx.check(hb.check_true(f"SCK pulses per read (gain {g})", bool(counts) and all(c == pulses for c in counts),
                                f"{pulses} per read", sorted(set(counts))))
    L.set_ok("afe.gain_channel", orig)


# ---------------------------------------------------------------------------------------- HG-18
DWT_BUDGETS = {0: 1000.0, 1: 2.0, 2: 1.0, 3: 1.0, 4: 1.0, 11: 1.0, 12: 1.0, 13: 1.0, 16: 1.0, 17: 1.0, 18: 1.0}
DWT_NAMES = {0: "main-loop pass", 1: "TIM2 step ISR (F2)", 2: "E-stop handler EXTI15_10 (F2)", 3: "START limit EXTI0",
             4: "END limit EXTI1", 5: "PAUSE EXTI9_5", 6: "EXTI4 HX711", 7: "TIM5 1 kHz tick", 8: "USART2 error",
             9: "DMA1 S5 RX", 10: "DMA1 S6 TX", 11: "CRIT_HALT (PRIMASK)", 12: "CRIT_AFE (BASEPRI 0x20)",
             13: "CRIT_MOTION (BASEPRI 0x20)", 14: "CRIT_DATA (BASEPRI 0x30)", 15: "CRIT_TICK (BASEPRI 0x40)",
             16: "hal_step_stop_now (PRIMASK)", 17: "hal_step_abort (PRIMASK)", 18: "hal_step_set_period_now (PRIMASK)",
             19: "SCK-high", 20: "HX711 shift-in", 21: "cal: empty stamp pair", 22: "cal: record call"}


def read_dwt(ctx: Ctx) -> dict[int, hb.DwtSection]:
    return {s: hb.DwtSection.from_words(s, ctx.L.meas.dwt(s)) for s in range(23)}


def merge_dwt(a: dict[int, hb.DwtSection], b: dict[int, hb.DwtSection]) -> dict[int, hb.DwtSection]:
    out = {}
    for s in a:
        x, y = a[s], b[s]
        if not x.count:
            out[s] = y
        elif not y.count:
            out[s] = x
        else:
            out[s] = hb.DwtSection(s, x.valid and y.valid, x.count + y.count, min(x.min_cycles, y.min_cycles),
                                   max(x.max_cycles, y.max_cycles), x.sum_cycles + y.sum_cycles,
                                   [p + q for p, q in zip(x.hist, y.hist)])
    return out


def hg18(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    dwt = "DWT" in ctx.shared.get("meas_info", {}).get("variant", [])
    for s in range(23):
        M.dwt(s, reset=True)
    # part A: 50 kHz stepping (PSU off, OI-E-HG-03) + 80 Hz stream + 20 cmd/s
    with psu_off_pulses(ctx, spm_val=2500.0, v_unhomed=20000, psu_back_on=True):
        L.ok("STREAM_START")
        jog(ctx, 20000)
        t_end = L.now_ns() + int(10e9 if not ctx.cfg.get("quick") else 2e9)
        k = 0
        while L.now_ns() < t_end:
            L.send(("GET_STATUS", "PING")[k % 2])
            if k % 2:
                L.send("JOG", {"v_um_s": 20000, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})
            k += 1
            L.advance(50.0)
        st_a = L.status()
        part_a = read_dwt(ctx)
        jog_stop(ctx)
    ctx.data("loop_max_us_50k", st_a["loop_max_us"])
    # part B: safety handlers exercised (real E-stop presses moving / idle, limits, PAUSE), PSU on
    for s in range(23):
        M.dwt(s, reset=True)
    for i in range(5):
        estop_trial(ctx, True, i)
    for i in range(3):
        clear_latches(ctx)
        enable(ctx)
        op.cue("PRESS-IDLE", "PRESS the red E-stop NOW (idle, enabled)",
               twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False), delay_ms=(50, 150))
        L.wait(400)
        release_estop_input(ctx)
    ready(ctx, 50_000)
    for i in range(3):
        n0 = L.n_events()
        jog(ctx, 10_000)
        L.jog_hold(10_000, 200)
        op.cue("PAUSE", "Press the PAUSE button NOW (table moving)",
               twin=lambda tw: (tw.act("button", name="pause", pressed=True)), delay_ms=(20, 80))
        L.wait_event("PAUSED", n0, 3000, jog_v=10_000)
        op.instruct("PAUSE-REL", "Release the PAUSE button", twin=lambda tw: tw.act("button", name="pause", pressed=False))
        wait_done(ctx, n0, 3000)
        L.cmd("HALT_CLEAR")
    orig_min, orig_max = int(L.get("limits.soft_min_um")), int(L.get("limits.soft_max_um"))
    for lim, key, far, start, v in (("START", "limits.soft_min_um", -10_000, 3_000, -1000),
                                    ("END", "limits.soft_max_um", 400_000, orig_max - 1_000, 1000)):
        ready(ctx, start, 30_000)
        with params(ctx, {key: far}):
            n0 = L.n_events()
            jog(ctx, v)
            L.wait_event("LIMIT_SET", n0, 40_000, jog_v=v)
            wait_done(ctx, n0, 3000)
            move_abs(ctx, start, 2_000)
    st_b = L.status()
    part_b = read_dwt(ctx)
    secs = merge_dwt(part_a, part_b)
    ctx.data("dwt", {s: vars(x) for s, x in secs.items()})
    ctx.data("dwt_part_a_step", vars(part_a[1]))
    if dwt:
        ov = hb.dwt_overhead(secs[21])
        ctx.check(hb.info("stamp-pair overhead", f"{ov[0]:.1f} ± {ov[1]:.1f} cycles", note="section 21 / INFO w6"))
        for s, b in DWT_BUDGETS.items():
            ctx.check(hb.check_dwt(f"{DWT_NAMES[s]} (section {s})", secs[s], b, ov,
                                   "F2 static bound 1.15 µs all-branch" if s == 2 else
                                   "F2 static bound 2.4 µs" if s == 1 else ""))
        sa = part_a[1]
        if sa.count:
            cpu = hb.cycles_us(sa.mean_cycles) * 50_000 / 1e6 * 100
            ctx.check(hb.check_le("step CPU at 50 kHz", [cpu], 15.0, hb.cycles_us(ov[1]) * 50_000 / 1e6 * 100, "%",
                                  note="mean step-ISR cycles × 50 kHz / 180 MHz"))
        for s in (5, 6, 7, 8, 9, 10, 14, 15, 19, 20):
            x = secs[s]
            ctx.check(hb.info(f"{DWT_NAMES[s]} (section {s})", f"max {hb.cycles_us(x.max_cycles):.2f} µs, n {x.count}"))
        ctx.check(hb.info("F2 E-stop handler coverage", "moving (jog 30 mm/s), idle-enabled; SAVE-time presses in HG-04",
                          note="all-branch static bound 1.15 µs is confirmed only for the branches exercised"))
    else:
        why = "DWT not modelled in the twin (w0 = 0)" if ctx.twin else "image is not HW_MEAS_DWT"
        for s, b in DWT_BUDGETS.items():
            ctx.check(hb.not_measured(f"{DWT_NAMES[s]} (section {s})", f"≤ {b} µs", why))
        ctx.check(hb.not_measured("step CPU at 50 kHz", "≤ 15 %", why))
    loop = max(st_a["loop_max_us"], st_b["loop_max_us"])
    ctx.check(hb.check_le("loop_max_us (FW self-report, NFR-006)", [loop], 1000.0, 1.0, "µs",
                          note="twin: loop time not modelled (reads 0)" if ctx.twin else "50 kHz + 80 Hz + 20 cmd/s"))
    ctx.check(hb.check_ge("stack_free_min", [min(st_a["stack_free_min"], st_b["stack_free_min"])], 1, 0, "bytes"))


# ---------------------------------------------------------------------------------------- HG-27
def hg27(ctx: Ctx) -> None:
    L = ctx.L
    ready(ctx, 30_000)
    L.ok("STREAM_START")
    rng = ctx.rng
    fb = int(L.get("stream.fallback_hz"))
    pos = L.status()["pos_um"]
    pid = [p.id for p in PBYKEY.values()]

    def one(k: int) -> list[tuple[str, dict | None]]:
        c = k % 16
        return [
            [("PING", None)], [("GET_INFO", None)], [("GET_STATUS", None)],
            [("GET_PARAM", {"id": rng.choice(pid)})], [("GET_ALL_PARAMS", {"page": rng.randrange(0, 3)})],
            [("SET_PARAM", {"id": PBYKEY["stream.fallback_hz"].id, "type": "u8", "value": fb})],
            [("SET_VALID", {"valid": 1})], [("STREAM_START", None)],
            [("DIAG_MEAS", {"op": 0, "sel": 0, "a": 0, "b": 0})], [("DIAG_MEAS", {"op": 3, "sel": 0, "a": 0, "b": 0})],
            [("STOP", {"mode": 1})], [("JOG", {"v_um_s": 0, "a_um_s2": 0, "bound_um": rc.JOG_NO_BOUND})],
            [("ENABLE", None)], [("MOVE_ABS", {"target_um": pos, "v_um_s": 10_000, "a_um_s2": 0})],
            [("HALT", None), ("HALT_CLEAR", None)], [("PAUSE", None), ("RESUME", None)]][c] + \
            ([("ESTOP_CLEAR", None), ("FAULT_CLEAR", None)] if k % 50 == 0 else [])

    rtts: dict[str, list[float]] = {}
    nack, sent = [], 0
    k = 0
    while sent < ctx.cfg["n_cmds"]:
        for name, f in one(k):
            r = L.cmd(name, f, timeout_ms=500)
            rtts.setdefault(name, []).append(r["_rtt_ms"])
            if r["status"] != "OK":
                nack.append((name, r["status"], r.get("detail")))
            sent += 1
        k += 1
        L.advance(10.0)
    allr = [x for v in rtts.values() for x in v]
    ctx.data("rtt_ms", {k: hb.stats(v) for k, v in rtts.items()})
    c = ctx.check(hb.check_le("command response (PC round trip, upper bound)", allr, 10.0, 0.0, "ms",
                              note=f"{sent} commands of {len(rtts)} types under streaming" +
                                   ("; twin: virtual time" if ctx.twin else "; MT-6 USB CDC")))
    if c.decision == hb.FAIL and not ctx.twin:
        c.decision = hb.INCONCL
        c.note += "; RTT > 10 ms is only an upper bound -> resolve on chip (J-EVT = RX, J-AUX = TX) before a verdict"
    ctx.check(hb.check_eq("unexpected NACKs", len(nack), 0, str(nack[:5])))
    nv = {}
    for name in ("SAVE_PARAMS", "LOAD_PARAMS", "DEFAULT_PARAMS", "LOAD_PARAMS"):
        r = L.cmd(name, timeout_ms=5000)
        nv.setdefault(name, []).append((r["status"], r["_rtt_ms"]))
    ctx.data("nvm", nv)
    ctx.check(hb.check_le("SAVE / LOAD / DEFAULT response", [x[1] for v in nv.values() for x in v], 2500.0, 0, "ms",
                          note="DEFAULT followed by LOAD restores the stored values"))
    ctx.check(hb.check_true("NVM commands OK", all(x[0] == "OK" for v in nv.values() for x in v), "status OK", nv))


# ============================================================================================ phase 4 (load)
def fit_spring(ctx: Ctx) -> None:
    ctx.op.instruct("SPRING", "Fit the spring specimen (contact ahead of the table in +x); hand at the E-stop",
                    twin=lambda tw: tw.act("specimen", kind="spring", k_n_per_mm=ctx.cfg["spring_k_n_per_mm"],
                                           x_contact_um=ctx.cfg["spring_contact_um"]))


def last_raw(ctx: Ctx, n: int = 8) -> tuple[float, float]:
    L = ctx.L
    L.ok("STREAM_START")
    k0 = len(L.frames)
    L.wait(n / 80 * 1000 + 40)
    raws = [d["afe_raw"] for d in L.data_since(k0) if d["afe_raw"] != rc.AFE_NO_DATA][-n:]
    return (statistics.fmean(raws), statistics.pstdev(raws) if len(raws) > 1 else 0.0) if raws else (float("nan"), 0.0)


def find_contact(ctx: Ctx) -> tuple[int, float]:
    """Advance at 1 mm/s until the raw rises by > 2000 counts over the unloaded value; returns (x_um, slope/mm)."""
    L = ctx.L
    xc_guess = ctx.cfg["spring_contact_um"] if ctx.twin else None
    start = (xc_guess - 5_000) if xc_guess else 50_000
    ready(ctx, start)
    raw0, _ = last_raw(ctx)
    x = start
    step = 1_000
    while x < 280_000:
        x += step
        move_abs(ctx, x, 5_000)
        r, _ = last_raw(ctx)
        if r - raw0 > 2_000:
            move_abs(ctx, x + 1_000, 1_000)
            r2, _ = last_raw(ctx)
            return x, (r2 - r) / 1.0
    raise BenchRefused("no spring contact found up to 280 mm")


def hg12(ctx: Ctx) -> None:
    L, M = ctx.L, ctx.L.meas
    fit_spring(ctx)
    ctx.jumpers("J-EVT <- PB4 (DOUT), J-PUL-A fitted")
    xc, slope = find_contact(ctx)
    ctx.data("contact", {"x_um": xc, "slope_counts_per_mm": slope})
    if slope <= 0:
        ctx.check(hb.check_true("spring slope", False, "raw rises with +x (sign convention, D-20)", slope))
        return
    x_start = xc + 500
    default_max = PBYKEY["safety.load_raw_max"].default
    lat_s, agree, first_ok = [], [], []
    psc = 17
    tick = hb.probe_tick_us(psc)
    for i in range(ctx.cfg["n_load_trials"]):
        L.set_ok("safety.load_raw_max", int(default_max))
        clear_latches(ctx)
        ready(ctx, x_start, 5_000)
        raw_now, _ = last_raw(ctx, 4)
        thr = int(raw_now + slope * ctx.rng.uniform(0.2, 0.8))
        L.set_ok("safety.load_raw_max", thr)
        n_evt0, n_pul0 = M.stamp_count("EVT"), M.stamp_count("PUL")
        M.probe_arm("DOUT", "RESET", True, psc)
        k0 = len(L.frames)
        n0 = L.n_events()
        jog(ctx, 500)
        fe = L.wait_event("FAULT_SET", n0, 5000, jog_v=500, step_ms=0.5)
        pr = M.probe_read()
        n_evt1 = M.stamp_count("EVT")
        wait_done(ctx, n0, 3000)
        if fe is None or fe["arg"] != FAULT_BIT["LOAD_LIMIT"]:
            first_ok.append(False)
            continue
        data = [d for d in L.data_since(k0) if d["afe_raw"] != rc.AFE_NO_DATA]
        viol = [d for d in data if d["afe_raw"] > thr]
        first_ok.append(bool(viol) and viol[0]["afe_raw"] == fe["value"])
        t_dec = viol[0]["t_us"] if viol else fe["t_us"]
        evs = M.stamp_entries("EVT", max(n_evt0, n_evt1 - 30), n_evt1 - 1)
        douts = [v for _, v in sorted(evs.items()) if v is not None and hb.sdiff32(t_dec, v) >= 0]
        last_pul = M.newest("PUL", 1)
        if douts and last_pul:
            d_edge = douts[-1]
            lat = max(hb.sdiff32(last_pul[0], d_edge), 0)
            lat_s.append(lat)
            later = [v for v in evs.values() if v is not None and hb.sdiff32(v, d_edge) > 0]
            if not later and "TRIGGERED" in pr["flags"]:
                agree.append(abs((pr["ccr2"] * tick if pr["ccr2"] else 0.0) - lat))
    L.set_ok("safety.load_raw_max", int(default_max))
    clear_latches(ctx)
    move_abs(ctx, xc - 2_000, 5_000)
    ctx.check(hb.check_true("trip on the first violating sample", bool(first_ok) and all(first_ok),
                            "FAULT_SET(LOAD_LIMIT).value == first DATA raw > threshold", f"{sum(first_ok)}/{len(first_ok)}"))
    ctx.check(hb.check_le("deciding DOUT edge -> last PUL (MT-4)", lat_s, 200.0, hb.u_stamp_pair_us(), "µs"))
    if agree:
        ctx.check(hb.check_le("MT-3 (RESET mode) vs MT-4 agree", agree, 2.0, 0, "µs",
                              note=f"{len(agree)} trials read before the next DOUT edge"))
    else:
        ctx.check(hb.not_measured("MT-3 (RESET mode) vs MT-4 agree", "≤ 2 µs",
                                  "the probe was never read before the next DOUT edge restarted it"))


def hg15(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    fit_spring(ctx)
    xc, slope = find_contact(ctx)
    raw0 = last_raw(ctx)[0] - slope * 1.0                  # ≈ unloaded raw (1 mm into contact at find_contact end)
    target = raw0 + ctx.cfg["preload_n"] * ctx.cfg["counts_per_n"]
    x = L.status()["pos_um"]
    while last_raw(ctx, 4)[0] < target and x < xc + 50_000:
        x += 500
        move_abs(ctx, x, 1_000)
    raw_pre, sig = last_raw(ctx, 20)
    ctx.data("preload", {"raw": raw_pre, "target": target, "sigma": sig})
    ctx.check(hb.check_ge("preload ≥ 98 N (raw)", [raw_pre], target, 0, "counts",
                          note=f"{ctx.cfg['counts_per_n']} counts/N (nominal cell; override with --counts-per-n)"))
    op.instruct("DIAL", "Set the dial indicator on the table and zero it")
    tol = max(4 * sig, 50.0) + 2 * slope / spm(ctx)       # + ≤ 2 steps issued between the last read and the hang
    for method in ("PIN", "POWER", "IWDG"):
        x0 = wx(ctx.tw) if ctx.twin else 0.0
        s0 = L.status()["pos_steps"]
        raw_b, _ = last_raw(ctx, 20)
        if method == "PIN":
            op.instruct("NRST", "Press and release the Nucleo RESET button (B2)", twin=lambda tw: tw.act("reset", cause="pin"))
            want = "PIN"
        elif method == "POWER":
            if not ctx.twin:
                ctx.L.tr.close_port()
            op.instruct("PWR", "Power-cycle the Nucleo only (USB / 5 V off 3 s, on); the 48 V PSU stays ON",
                        twin=lambda tw: tw.act("reset", cause="power"))
            if not ctx.twin:
                ctx.L.tr.reopen()
            want = "POWER_ON"
        else:
            enable(ctx)
            jog(ctx, -10)                                         # toward unloading, ≈ 0.01 step/ms
            L.jog_hold(-10, 150)
            s_hang = L.status()["pos_steps"]
            M.hang("MAIN", 0)
            L.advance(400.0)
            want = "IWDG"
        L.wait(300, keepalive=False)
        L.cmd("PING")
        st = L.status()
        c, _ = M.counter()
        raw_a, _ = last_raw(ctx, 20)
        dpos_mm = (s_hang - s0) / spm(ctx) if method == "IWDG" else 0.0     # pos_steps restarts at boot
        dial = op.ask_number(f"DIAL-{method}", "Dial indicator reading now", "mm",
                             twin=lambda tw: (wx(tw) - x0) / 1000.0, nominal=0.0)
        ctx.check(hb.check_eq(f"{method}: reset cause", st["reset_cause"], want))
        ctx.check(hb.check_eq(f"{method}: MT-2 = 0 from boot until ENABLE", c, 0))
        ctx.check(hb.check_abs_le(f"{method}: raw change (minus commanded motion × slope)",
                                  [raw_a - raw_b - slope * dpos_mm], tol, 0, "counts",
                                  note=f"noise σ {sig:.0f} counts, slope {slope:.0f} counts/mm"))
        ctx.judged(hb.check_abs_le(f"{method}: axis motion (dial − commanded)", [(dial.value or 0) - dpos_mm],
                                   0.01, ctx.cfg["dial_u_mm"] + (2 / spm(ctx) if method == "IWDG" else 0), "mm",
                                   note="D-13: the driver keeps holding"), dial)
    if ctx.twin:
        ctx.note("twin: the axis model is not back-driven by the spring when ENA is released, so the dial check cannot "
                 "fail in the twin; the FW part (counter 0, ENA left enabled at boot) is exercised")
    # unload
    enable(ctx)
    jog(ctx, -1000)
    zr = raw0 + PBYKEY["safety.release_band_raw"].default
    t_end = L.now_ns() + int(30e9)
    k0 = len(L.frames)
    while L.now_ns() < t_end:
        L.jog_hold(-1000, 200)
        d = [x["afe_raw"] for x in L.data_since(k0) if x["afe_raw"] != rc.AFE_NO_DATA]
        if d and d[-1] < zr:
            break
    jog_stop(ctx)
    ready(ctx)


def hg25(ctx: Ctx) -> None:
    L, M, op = ctx.L, ctx.L.meas, ctx.op
    cal = op.ask_yes_no("CAL", "Travel calibration done (motion.steps_per_mm calibrated with the SW wizard, M3)?",
                        nominal=True)
    if not cal.value:
        ctx.check(hb.not_measured("speed envelope", "after travel calibration", "travel calibration not done"))
        return
    op.instruct("NOSPEC", "Remove the spring specimen (unloaded speed envelope)",
                twin=lambda tw: tw.act("specimen", kind="none"))
    sp = spm(ctx)
    a = float(L.get("motion.a_max_um_s2"))

    ref = {}

    def timed_move(x_from: int, x_to: int, v: int) -> tuple[float, int]:
        move_abs(ctx, x_from, 20_000)
        ref["x"] = wx(ctx.tw) if ctx.twin else 0.0
        if ref.get("prompt"):
            op.instruct(ref["prompt"][0], ref["prompt"][1])
        n_pul0 = M.stamp_count("PUL")
        n0 = L.n_events()
        L.ok("MOVE_ABS", {"target_um": x_to, "v_um_s": v, "a_um_s2": 0})
        first = None
        for _ in range(200):
            first = stamp(ctx, "PUL", n_pul0)
            if first is not None:
                break
            L.advance(1.0)
        wait_done(ctx, n0, abs(x_to - x_from) / v * 1000 * 1.5 + 10_000)
        last = M.newest("PUL", 1)[0]
        n = M.stamp_count("PUL") - n_pul0
        return hb.sdiff32(last, first) / 1e6 if first is not None else float("nan"), n

    ready(ctx, 10_000)
    ref["prompt"] = ("DIAL0", "Dial indicator on the table at 10 mm, zeroed")
    T, n = timed_move(10_000, 12_000, 10)
    d = op.ask_number("DIAL1", "Dial indicator travel after the 0.01 mm/s move", "mm",
                      twin=lambda tw: (wx(tw) - ref["x"]) / 1000.0, nominal=2.0)
    v_meas = (d.value or 0) * 1000 / T if T == T and T > 0 else 0
    ctx.data("slow", {"T_s": T, "pulses": n, "fw_um_s": (n - 1) / sp * 1000 / T if T else 0})
    ctx.judged(hb.check_abs_le("0.01 mm/s over 2 mm: speed error", [(v_meas - 10) / 10 * 100], 1.0, 0.5, "%",
                               note="dial u 0.5 %; duration from MT-4 first / last PUL"), d)
    ref["prompt"] = ("CAL0", "Caliper reference on the table at 20 mm")
    T, n = timed_move(20_000, 70_000, 10_000)
    c = op.ask_number("CAL50", "Caliper: travel of the 10 mm/s move (20 -> 70 mm)", "mm",
                      twin=lambda tw: (wx(tw) - ref["x"]) / 1000.0, nominal=50.0)
    dm = (c.value or 0) * 1000
    disc = (a * T) ** 2 - 4 * a * dm
    v_c = (a * T - math.sqrt(disc)) / 2 if disc >= 0 else float("nan")   # trapezoid: d = v·T − v²/a
    ctx.judged(hb.check_abs_le("10 mm/s over 50 mm: cruise speed error", [(v_c - 10_000) / 10_000 * 100], 1.0, 0.04, "%",
                               note=f"T {T:.4f} s, ramps removed with a = {a:g} µm/s²"), c)
    n0 = L.n_events()
    move_abs(ctx, 280_000, 30_000)
    move_abs(ctx, 10_000, 30_000)
    alm = [e for e in L.events_since(n0, "ALM_CHANGED")]
    ctx.check(hb.check_true("30 mm/s unloaded: no ALM", not alm and "ALM" not in L.status()["io"], "no ALM_CHANGED", alm))
    n0 = L.n_events()
    home(ctx)
    hm = L.events_since(n0, "HOMED")
    st = L.status()
    ctx.check(hb.check_le("re-home drift", [abs(hm[0]["value"]) if hm else 1e9], float(L.get("home.drift_tol_um")),
                          1000.0 / sp, "µm", note="HOMED value"))
    ctx.check(hb.check_true("no HOME_DRIFT fault", "HOME_DRIFT" not in st["faults"], "", st["faults"]))
    lo, hi = int(L.get("limits.soft_min_um")), int(L.get("limits.soft_max_um"))
    move_abs(ctx, lo, 30_000)
    x2 = wx(ctx.tw) if ctx.twin else 0.0
    move_abs(ctx, hi, 30_000)
    t = op.ask_number("TRAVEL", "Caliper / scale: travel soft_min -> soft_max", "mm",
                      twin=lambda tw: (wx(tw) - x2) / 1000.0, nominal=(hi - lo) / 1000)
    ctx.check(hb.check_ge("usable travel (FW)", [(hi - lo) / 1000], 280.0, 0, "mm"))
    ctx.judged(hb.check_ge("usable travel (measured)", [t.value or 0], 280.0, 0.05, "mm"), t)
    move_abs(ctx, 10_000, 30_000)


def hg26(ctx: Ctx) -> None:
    L, op = ctx.L, ctx.op
    ready(ctx)
    op.instruct("DIAL", "Dial indicator touching the table at x = 0 (after HOME), zeroed")
    x0 = wx(ctx.tw) if ctx.twin else 0.0
    vals, drifts, answers = [], [], []
    for i in range(ctx.cfg["n_home_rep"]):
        move_abs(ctx, 20_000, 20_000)
        n0 = L.n_events()
        home(ctx)
        hm = L.events_since(n0, "HOMED")
        drifts.append(hm[0]["value"] if hm else None)
        a = op.ask_number(f"DIAL{i}", "Dial reading at x = 0 after HOME", "mm", twin=lambda tw: (wx(tw) - x0) / 1000.0,
                          nominal=0.0)
        answers.append(a)
        vals.append(a.value or 0.0)
    two_sigma = 2 * statistics.pstdev(vals) if len(vals) > 1 else 0.0
    ctx.judged(hb.check_le("home repeatability (2σ)", [two_sigma], 0.02, ctx.cfg["dial_u_mm"], "mm",
                           note=f"{len(vals)} cycles"), *answers)
    ctx.check(hb.info("FW HOMED drift values (µm)", drifts))


def hg23(ctx: Ctx) -> None:
    op = ctx.op
    a = op.ask_yes_no("INSP", "SN74ACT244 buffer board installed (SYS-011): 5 V supply, pull-downs, outputs to PUL+/DIR+ "
                      "and via D1 to ENA+ (wiring §2 stage 2)?", nominal=True)
    ctx.judged(hb.check_true("buffer board installed", a.value, "before the first calibration (D-28)"), a)
    op.instruct("TAPS", "Move the MH taps J-PUL-A / J-PUL-B / J-DIR / J-ENA to the buffer outputs")
    hg06(ctx, "buffer")
    hg07(ctx, "buffer")
    hg08(ctx, "buffer")


# ============================================================================================ release image
def hg30(ctx: Ctx) -> None:
    L, op = ctx.L, ctx.op
    verify_image(ctx, "release")
    hg32(ctx, repeat=True)
    # E-stop: latch, ENA disabled = shaft free, EVENT sequence
    ready(ctx, 20_000)
    n0 = L.n_events()
    jog(ctx, 10_000)
    L.jog_hold(10_000, 300)
    op.cue("PRESS", "PRESS the red E-stop NOW (table moving)",
           twin=lambda tw: tw.act("estop", open=True, drv_power_follows=False), delay_ms=(20, 100))
    L.wait_event("ESTOP_SET", n0, 3000, jog_v=10_000)
    L.wait(100)
    st = L.status()
    seq = [e["code"] for e in L.events_since(n0)]
    free = op.ask_yes_no("FREE", "Shaft free by hand?", twin=lambda tw: not tw.act("query", what="outputs")["ena_enabled"],
                         nominal=True)
    ok = all(c in seq for c in ("ESTOP_SET", "STOPPED", "MOVE_DONE")) and "ESTOP" in st["flags"] and "ENA_DISABLED" in st["io"]
    ctx.check(hb.check_true("E-stop: latch + ENA disabled + EVENTs", ok, "ESTOP_SET, STOPPED(ESTOP), MOVE_DONE", seq))
    ctx.judged(hb.check_true("E-stop: shaft free", free.value, "driver disabled"), free)
    release_estop_input(ctx)
    # limit stop + latch (real START actuation at 1 mm/s)
    ready(ctx, 3_000)
    with params(ctx, {"limits.soft_min_um": -10_000}):
        n0 = L.n_events()
        jog(ctx, -1000)
        ev = L.wait_event("LIMIT_SET", n0, 30_000, jog_v=-1000)
        wait_done(ctx, n0, 3000)
        st_ev = [e for e in L.events_since(n0, "STOPPED")]
        r = jog(ctx, -1000)
        ctx.check(hb.check_true("limit stop + latch", ev is not None and bool(st_ev) and st_ev[0]["arg"] == STOP_CAUSE["LIMIT_START"]
                                and r["status"] == "E_STATE", "LIMIT_SET, STOPPED(LIMIT_START), JOG toward refused",
                                {"stopped": st_ev[:1], "jog": r}))
        move_abs(ctx, 3_000, 2_000)
    # load-limit trip (spring from phase 4, if still fitted)
    if ctx.twin:
        fit_spring(ctx)                      # the re-flashed twin starts without the phase-4 specimen
    sp = op.ask_yes_no("SPRING", "Is the spring specimen fitted (load-limit repeat)?",
                       twin=lambda tw: tw.world.get("specimen", {}).get("kind") == "spring", nominal=False)
    if sp.value:
        xc, slope = find_contact(ctx)
        raw, _ = last_raw(ctx, 4)
        with params(ctx, {"safety.load_raw_max": int(raw + slope * 0.3)}):
            n0 = L.n_events()
            jog(ctx, 500)
            fe = L.wait_event("FAULT_SET", n0, 5000, jog_v=500)
            wait_done(ctx, n0, 3000)
        ctx.check(hb.check_true("load-limit trip", fe is not None and fe["arg"] == FAULT_BIT["LOAD_LIMIT"], "FAULT_SET(LOAD_LIMIT)",
                                fe))
        clear_latches(ctx)
        move_abs(ctx, xc - 2_000, 5_000)
    else:
        ctx.judged(hb.manual("load-limit trip", "FAULT_SET(LOAD_LIMIT)", note="spring not fitted"), sp)
    # STOP / HALT / PAUSE
    for kind, cause in (("STOP", "PC_STOP"), ("HALT", "PC_HALT"), ("PAUSE", "PC_PAUSE")):
        ready(ctx, 50_000)
        n0 = L.n_events()
        jog(ctx, 10_000)
        L.jog_hold(10_000, 300)
        L.cmd(kind, {"mode": 0} if kind == "STOP" else None)
        wait_done(ctx, n0, 3000)
        stp = L.events_since(n0, "STOPPED")
        ctx.check(hb.check_true(f"{kind} during a jog", bool(stp) and stp[0]["arg"] == STOP_CAUSE[cause], f"STOPPED({cause})",
                                stp[:1]))
        if kind == "HALT":
            L.cmd("HALT_CLEAR")
        if kind == "PAUSE":
            ctx.check(hb.check_eq("RESUME clears PAUSED", L.cmd("RESUME")["status"], "OK"))
    md = home(ctx)
    ctx.check(hb.check_true("HOME", md["arg"] == MD["TARGET"], "MOVE_DONE TARGET, HOMED", md))
    res = soak(ctx, ctx.cfg["rel_soak_s"])
    ctx.check(hb.check_eq("2-min soak: seq gaps / CRC / FW errors",
                          res["seq_gaps"] + res["pc_crc"] + sum(res["fw"].values()) - res["fw"]["event_overflows"], 0,
                          f"{res['data']} DATA frames in {res['seconds']:.0f} s"))
    st = L.status()
    ctx.check(hb.check_le("loop_max_us (release image)", [st["loop_max_us"]], 1000.0, 1.0, "µs",
                          note="twin: not modelled" if ctx.twin else ""))
    a = ctx.op.ask_yes_no("HG31", "HG-31: the release image runs with the MH jumpers fitted (no pin in analog mode — A's "
                          "statement) — confirmed?", nominal=True)
    ctx.judged(hb.check_true("HG-31 release image with jumpers fitted", a.value, "no loopback pin analog"), a)


def hg21(ctx: Ctx) -> None:
    a = ctx.op.ask_yes_no("FITTED", "Is the optional 48 V presence sense on PA7 fitted (wiring §7.2)?",
                          nominal=ctx.cfg["power_sense_fitted"])
    if not a.value:
        ctx.check(hb.na("DRV_PWR sense reaction", "≤ 25 ms", "no presence sense fitted (D-41, CR-03 default)"))
        return
    ctx.check(hb.manual("DRV_PWR sense reaction", "ENA disabled and last PUL ≤ 25 ms after the PA7 change",
                        note="procedure: set drv.pwr_sense_enable = 1 (SAVE + REBOOT), J-EVT <- PA7, PROBE_ARM DRV_PWR "
                             "TRIGGER PSC 179, PSU off during a 2 mm/s jog; restore the parameter"))


def hg22(ctx: Ctx) -> None:
    ctx.check(hb.na("K1_WELDED", "N/A", "no contactor since D-41; feature verified in the twin (TC-SAF-FW-025-01)"))


# ============================================================================================ runner glue
def run_item(ctx: Ctx, item_id: str, title: str, fn: Callable[[Ctx], None], req: str = "", method: str = "") -> ItemRec:
    it = ctx.begin(item_id, title, req, method)
    try:
        fn(ctx)
    except BenchRefused as e:
        it.error = f"REFUSED: {e}"
    except Exception as e:  # noqa: BLE001
        it.error = f"{type(e).__name__}: {e}"
        it.notes.append(traceback.format_exc(limit=6))
        try:
            ctx.L.cmd("HALT", timeout_ms=500)            # any error inside a procedure: holding stop first
            ctx.L.cmd("HALT_CLEAR", timeout_ms=500)
        except Exception:  # noqa: BLE001
            pass
    return ctx.end()

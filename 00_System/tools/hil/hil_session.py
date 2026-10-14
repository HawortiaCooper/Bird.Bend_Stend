#!/usr/bin/env python3
"""HG1 hardware-gate session runner (FW_test_plan v0.4.1 §6; order §6.7; bench safety procedure §6.8).

Owner: Validator E (00_System/tools/hil, the Integrator reviews). Runbook: HW_GATE_RUNBOOK.md (same folder).

  Twin dry run (no hardware, D-06 respected — the only mode allowed before the PO's approval):
    .venv\\Scripts\\python 00_System\\tools\\hil\\hil_session.py --twin --out <dir> [--quick] [--only HG-09,HG-14]

  Board session (ONLY with the PO's approval file, D-57 b; normally through the frozen gate runner):
    BirdBendStand-GateRunner.exe --approval approval_D-06-GATE-20261020.json --port COM7 --out <dir>
        [--from HG-28] [--only ...]        (source run: python 00_System\\tools\\hil\\gate_runner.py …)

  Other:  --list (the ordered plan) · --report <dir> (re-render the markdown report)

D-06 interlock (D-57 b, gate_approval / hil_link.open_serial): no serial port is opened without a valid approval
file (schema, body hash, D-06-GATE reference row naming the board UID, validity window ≤ 7 days containing today,
runner hash = this runner, kit .hex hashes = the files in --delivery, dictionary hash = the runner's), an explicit
--port and the operator re-typing the port name; the board UID and the accepted-open items come from the file only.
--twin and --port are exclusive. The repository lookup (--approved REF in DECISIONS.md / STATUS.md) is gone from the
session CLI; make_approval.py applies it when the Orchestrator generates the file.
Session identity (FWR-13 / FWR-15, FW_code_review.md): every run starts with the identity block (S-00, or S-ID on a
resume) whatever --only / --from select: GET_INFO dict hash + protocol must match and, on the board, the UID must equal
--board-uid, else the session stops. Results carry session id / mode / board UID / image identity; gates (load gate,
§6.8 bench entry, HG-10 c/d) accept only results of the current session. HG-10a always runs between BENCH-ENTRY and
BENCH-EXIT; phase-4 items always follow GATE-LOAD. --accepted-open references must be recorded accepted rows.
Flashing is never done by this tool: image steps ask the PO to flash and then verify GET_INFO / DIAG_MEAS INFO.
Results: <out>/results/<item>.json, <out>/session.json, <out>/HG_report_<mode>.md, <out>/prompts.jsonl,
<out>/raw/frames.bin, <out>/runner.json and the results zip <out>/gate_<date>_<uid>_<session>.zip (gate_export, D-57 c).
Everything is written below <out> only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gate_approval as ga  # noqa: E402
import gate_export  # noqa: E402
import gate_res  # noqa: E402
import hil_budget as hb  # noqa: E402
import hil_procs as P  # noqa: E402
import hil_link as hl  # noqa: E402
import hil_report  # noqa: E402
import ref_codec as rc  # noqa: E402
from hil_link import InterlockError, Link, LoggedTransport, SerialTransport, TwinTransport  # noqa: E402
from hil_operator import ConsoleOperator, OperatorAbort, TwinOperator  # noqa: E402


@dataclass(frozen=True)
class Step:
    id: str
    phase: int
    title: str
    fn: Callable
    req: str = ""
    method: str = ""
    auto: str = "auto"          # auto | partial | manual  (what the twin dry run can exercise)
    image: str = "meas"


# ---------------------------------------------------------------------------------------- session steps
def s_start(ctx: P.Ctx) -> None:
    op = ctx.op
    P.check_identity(ctx)                    # FWR-15: first, before any operator question or motion (refuses)
    p = op.ask_text("PERSONS", "Operator and observer names", nominal="(dry run)")
    ctx.data("persons", p.value)
    kit = ctx.cfg.get("kit")
    if kit:                                            # D-57 b: the approved kit names commit and images
        ctx.data("images_commit", kit.get("commit"))
        ctx.data("kit_images", {k: {"path": v.get("path"), "sha256": v.get("sha256"), "build": v.get("build")}
                                for k, v in kit.get("images", {}).items()})
    else:
        c = op.ask_text("COMMIT", "Git commit of the three images (release / HW_MEAS / HW_MEAS_DWT, one commit)",
                        nominal="(dry run)")
        ctx.data("images_commit", c.value)
    dip = op.ask_yes_no("DIP", "Driver DIP target setting applied with the driver unpowered (D-27, wiring §10)?",
                        nominal=True)
    ctx.judged(hb.check_true("DIP applied before the first motion", dip.value, "D-27"), dip)
    if not dip.value:
        raise P.BenchRefused("DIP not applied (D-27) - no motion, session stopped (O-2)")
    verify_bench(ctx)
    ctx.check(hb.info("TC-SYS-009-02 / check_meas_build", "see HG-29 f"))


def verify_bench(ctx: P.Ctx) -> None:
    """GR3-2: the bench facts A's pictures mark "ASSUMED - verify at HW gate" are confirmed (or corrected) by the
    operator before the first motion; the load-cell side and the caliper faces are preconditions of HG-09 / 12 / 15 /
    25 / 28 (S-00 FAIL stops the session)."""
    op = ctx.op
    t = op.ask_text("VERIFY-ESTOP", "E-stop contact block terminal numbers on YOUR button: NC (sense, to PA10) and NO "
                    "(D-42 path to R_E) - pictures B6 / B3 assume NC 11-12, NO 13-14; type e.g. 'NC 11-12, NO 13-14'",
                    nominal="NC 11-12, NO 13-14")
    ctx.data("estop_terminals", t.value)
    ctx.check(hb.info("E-stop terminals (operator)", t.value))
    n = op.ask_yes_no("VERIFY-NUCLEO", "Nucleo parts where picture B7 shows them: black B2 RESET, blue B1 USER, green LD2, "
                      "JP5 on U5V (the USB cable powers the board)?", nominal=True)
    ctx.data("nucleo_layout_as_b7", n.value)
    ctx.check(hb.info("Nucleo layout as picture B7 (operator)", n.value,
                      note="" if n.value else "layout differs: the operator uses the board silkscreen"))
    c = op.ask_yes_no("VERIFY-CELL", "Load cell on the START side (picture B8): does a move AWAY from START (+x) PULL the "
                      "spring (tension, +F)? Check by looking at the mechanics; nothing moves now", nominal=True)
    ctx.judged(hb.check_true("load cell on the START side, +x = tension", c.value,
                             "preload direction of HG-12 / HG-15 (picture B8)"), c)
    f = op.ask_yes_no("VERIFY-CALIPER", "Two flat caliper reference faces chosen and marked with a pen (picture B9): a "
                      "fixed face at the START end and the table's -x face?", nominal=True)
    ctx.judged(hb.check_true("caliper reference faces marked", f.value, "HG-09 / HG-25 / HG-28 readings (picture B9)"), f)
    if not (c.value and f.value):
        raise P.BenchRefused("bench facts not confirmed (load-cell side / caliper faces, GR3-2) - no motion, session "
                             "stopped; correct the bench or ask Validator E to adapt the procedure")


def s_identity(ctx: P.Ctx) -> None:
    """Resume of a session (--from / --only after S-00 passed): board / image identity re-checked (FWR-15)."""
    P.check_identity(ctx)


def image_step(image: str) -> Callable[[P.Ctx], None]:
    def fn(ctx: P.Ctx) -> None:
        # OI-A-DLV-06: the PO flashes the delivered .hex files (SHA256SUMS.txt, BUILD_INFO.txt), method B2 of
        # FLASHING.md - never the .bin (it starts at 0x08000000 and wipes the NVM sectors 1-2). The gate images live
        # only in the gate kit, the release image in the bring-up package (runbook v0.4).
        names = {"meas": "GateKit_FW<rel>/BBS_FW_<rel>_HW_MEAS_GATE-ONLY.hex (nucleo_f446re_meas, HW_MEAS)",
                 "meas_dwt": "GateKit_FW<rel>/BBS_FW_<rel>_HW_MEAS_DWT_GATE-ONLY.hex (nucleo_f446re_meas_dwt, HW_MEAS_DWT)",
                 "release": "BringUp_FW<rel>/firmware/BBS_FW_<rel>_release.hex (nucleo_f446re, release)"}
        for k, v in ((ctx.cfg.get("kit") or {}).get("images") or {}).items():      # D-57 b: the approved file
            names[k] = f"{v.get('path')} (build {v.get('build')}, SHA-256 {str(v.get('sha256'))[:16]}…)"
        if ctx.twin:
            ctx.L.tr.reflash(image)
            ctx.note(f"twin: engine restarted with hw_meas={image != 'release'} (stand-in for flashing {names[image]})")
        else:
            ctx.identified = False                    # C-R3: the device on the port is unverified until verify_image
            ctx.L.tr.close_port()
            ctx.op.instruct("FLASH", f"PO: flash {names[image]} (04_Delivery, recorded commit) "
                            "(SHA-256 as in SHA256SUMS.txt) with STM32CubeProgrammer over the ST-LINK, method B2: NO full "
                            "chip erase and never the .bin (both wipe the saved configuration, NVM sectors 1-2); the "
                            "session closed the COM port. Board resets.")
            ctx.L.tr.reopen()
        ctx.image = image
        ctx.L.wait(400, keepalive=False)
        ctx.L.cmd("PING")
        P.verify_image(ctx, image)
    return fn


def p2_entry(ctx: P.Ctx) -> None:
    ctx.op.instruct("P2", "Phase 2: driver PSU ON (48 V), direct 3.3 V drive, no specimen, nothing in the travel; "
                    "observer at the red button", twin=lambda tw: tw.act("drv_power", on=True))
    ctx.L.wait(600)
    ctx.check(hb.info("phase 2 entry", "driver powered"))


LOAD_PREREQ = ("HG-01", "HG-02", "HG-03", "HG-04", "HG-05", "HG-06", "HG-07", "HG-08", "HG-09", "HG-10cd", "HG-10a",
               "HG-10b", "HG-11", "HG-12", "HG-13", "HG-14", "HG-16", "HG-17", "HG-18", "HG-19", "HG-20", "HG-23",
               "HG-24", "HG-28", "HG-29", "HG-32")


def gate_load(ctx: P.Ctx) -> None:
    """SYS-009: no motion under load before HG-01…24, 28, 29, 32 pass (plan §6.5; HG-12 is itself the first load)."""
    accepted = ctx.cfg.get("accepted_open", {})
    missing = []
    for i in LOAD_PREREQ:
        if i == "HG-12":
            continue
        r = ctx.result_of(i)
        v = r["verdict"] if r else "NOT RUN"
        if v in (hb.PASS, hb.NA) or i in accepted:
            continue
        missing.append(f"{i}: {v}")
    ctx.data("accepted_open", accepted)
    ctx.data("missing", missing)
    crit = "HG-01…24, 28, 29, 32 PASS / N/A / accepted"
    if ctx.twin:
        ctx.check(hb.info("SYS-009 prerequisites for load (twin: reported, not enforced)", f"{len(missing)} open",
                          note="; ".join(missing[:8])))
    else:
        ctx.check(hb.check_true("SYS-009 prerequisites for load", not missing, crit, missing[:12]))
    if missing and not ctx.twin:
        raise P.BenchRefused("SYS-009: motion under load refused — open items: " + "; ".join(missing))
    if missing:
        ctx.note("twin dry run: the gate is reported but not enforced (MANUAL items cannot pass in the twin)")


def hg10cd(ctx: P.Ctx) -> None:
    P.hg10cd(ctx)


IDENTITY_ITEMS = P.IDENTITY_ITEMS
#: what the operator does by hand when the session stops with the device unidentified (no BENCH-EXIT is sent)
MANUAL_RESTORE = ("Make the bench safe by hand: 48 V PSU OFF; if the E-stop sense was bypassed, remove J-STIM, "
                  "reconnect the sense plug and remove the tag (§6.8 P-8); then report to the product owner")


def stop_reason(item_id: str, it) -> str | None:
    """O-2 / C-R3: an identity item (S-00, S-ID, IMG-*) or BENCH-ENTRY that ended with an error, a FAIL or an
    INCONCLUSIVE verdict stops the session before any further motion (BENCH-ENTRY: after BENCH-EXIT restored the
    E-stop sense). None = continue."""
    if item_id not in IDENTITY_ITEMS and item_id != "BENCH-ENTRY":
        return None
    v = "ERROR" if it.error else it.verdict
    if it.error or v in (hb.FAIL, hb.INCONCL):
        return f"{item_id} ended {v}" + (f" ({it.error})" if it.error else "")
    return None


def unidentified_reason(ctx: P.Ctx, item_id: str, *, before: bool) -> str | None:
    """C-GR-4 (belt and braces, board sessions only): no item other than an identity item (S-00, S-ID, IMG-*) may START
    while the device on the port is unidentified, and ANY item that ENDS with the device unidentified (e.g. a wrong or
    missing board after a re-plug inside HG-01 / HG-20 / HG-15) stops the session. None = continue."""
    if ctx.twin or ctx.identified:
        return None
    if before and item_id in IDENTITY_ITEMS:
        return None
    return (f"device not identified before {item_id} - nothing is sent to it" if before else
            f"{item_id} ended with the device not identified (wrong / missing board after a re-plug?)")


def validate_selection(only: str | None, from_: str | None) -> None:
    """O-3: --only / --from ids are checked before any port is opened (ValueError)."""
    ids = {s.id for s in PLAN} | {S_ID.id}
    if only:
        unknown = {x.strip() for x in only.split(",") if x.strip()} - ids
        if unknown:
            raise ValueError(f"--only: unknown step id(s) {sorted(unknown)}")
    if from_:
        pool = [s.id for s in PLAN] if not only else [s.id for s in PLAN if s.id in {x.strip() for x in only.split(",")}]
        if from_ not in pool:
            raise ValueError(f"--from {from_!r} is not in the selected plan")
S_ID = Step("S-ID", 0, "Resume: board and image identity re-check (FWR-15)", s_identity, "SYS-009, D-06")

PLAN: list[Step] = [
    Step("S-00", 0, "Session start: persons, image commit, board identity, DIP applied", s_start, "SYS-009, D-06",
         auto="partial"),
    Step("IMG-MEAS", 0, "PO flashes HW_MEAS; image verified", image_step("meas"), "TC-SYS-009-02"),
    # phase 1 — driver PSU off
    Step("HG-01", 1, "Board identity, solder bridges, MH header continuity (C-01)", P.hg01, "SYS-007, FW-PLT-002",
         "visual + photo; DMM continuity incl. the 1 kΩ / 220 Ω", "manual"),
    Step("HG-19", 1, "Driver DIP sheet (C-19)", P.hg19, "SYS-005", "inspection of SW1…SW8", "manual"),
    Step("HG-20", 1, "E-stop circuit D-41 / D-42 (C-20)", P.hg20, "SYS-006, SYS-001", "inspection + DMM M-5", "manual"),
    Step("HG-31", 1, "Loopback hygiene (C-24)", P.hg31, "SYS-009", "init-code statement; release image at HG-30", "manual"),
    Step("HG-29", 1, "Measurement-chain self-test (C-24)", P.hg29, "SYS-009",
         "INFO; STIM -> MT-3 / MT-4; 10 kHz pulse train (PSU off): MT-2 = Δpos = stamps, PWM period; RC delays",
         "partial"),
    Step("HG-03", 1, "VCP 921 600 Bd soak (C-03)", P.hg03, "IF-002, SYS-009", "80 Hz stream + 20 cmd/s, counters"),
    Step("HG-02", 1, "Clock source and PUL frequency (C-02)", P.hg02, "FW-PLT-002",
         "CLK_FALLBACK; MT-5 regression; MT-3 PWM input at 50 kHz (PSU off)"),
    # phase 2 — driver powered, direct drive, no specimen
    Step("P2-ENTRY", 2, "Phase 2 entry: driver PSU on", p2_entry, auto="partial"),
    Step("HG-32", 2, "D-41 / CR-03 configuration (C-26)", P.hg32, "SYS-009, D-41", "GET_PARAM defaults; E-stop held > 1 s"),
    Step("HG-06", 2, "Opto drive margin (C-06)", P.hg06, "SYS-009, SYS-011", "STATIC_LEVEL + DMM (VOH, 100 Ω shunt)",
         "partial"),
    Step("HG-10cd", 2, "D-42 hardwired ENA cut: MCU in reset, DMM M-1…M-4 (C-25) — before any bypass", hg10cd,
         "SAF-FW-005, SYS-006, D-42", "functional by hand + DMM; FW restart sequence", "partial"),
    Step("HG-28", 2, "First motion, direction, homing smoke (SYS-009)", P.hg28, "SYS-009, FW-HOM-001/002",
         "un-homed 1 mm/s jog, caliper; HOME; HOME with inverted DIR", "partial"),
    Step("HG-07", 2, "ENA enable / disable and settle (C-07)", P.hg07, "FW-MOT-008",
         "MT-3 trigger on the ENABLE frame (RX), CCR3 = ENA edge, MT-4 first PUL"),
    Step("HG-08", 2, "PUL / DIR timing (C-08)", P.hg08, "FW-MOT-001",
         "PWM input ≥ 1e5 pulses at 50 kHz (PSU off); 1-step reversals: MT-3 on DIR + MT-4"),
    Step("HG-09", 2, "Step count integrity (C-09)", P.hg09, "SAF-FW-004",
         "MT-2 vs Δpos_steps: random moves, jogs with reversals (stamp segments), random STOP / HALT; caliper",
         "partial"),
    Step("BENCH-ENTRY", 2, "§6.8 P-1…P-6: E-stop sense bypass (J-STIM), HG-29 e E-stop input", P.bench_entry,
         "SYS-009", auto="partial"),
    Step("HG-10a", 2, "E-stop reaction, 100 STIM trials (C-10 a)", P.hg10a, "SAF-FW-005, SYS-006",
         "MT-3 trigger PSC 17 on the E-stop node, CCR2 last PUL, CCR3 ENA; 30 mm/s jog; re-home per trial"),
    Step("BENCH-EXIT", 2, "§6.8 P-8: sense restored — before any further motion", P.bench_exit, "SYS-009"),
    Step("HG-10b", 2, "E-stop real presses + slow press, FW with forced ENA (C-10 b/e)", P.hg10b,
         "SAF-FW-005, SYS-006, D-42", "10 real presses during a jog; 2 slow presses", "partial"),
    Step("HG-11", 2, "Limit reaction (C-11)", P.hg11, "SAF-FW-002",
         "100 STIM trials START / END (switch unplugged) MT-3 PSC 1; 10 real actuations at 1 mm/s"),
    Step("HG-13", 2, "PC STOP / HALT (C-13)", P.hg13, "SAF-FW-002, NFR-003",
         "100 STOP 0 + 100 HALT after ≥ 5 ms silence; MT-3 trigger on RX, last byte end -> last PUL"),
    Step("HG-17", 2, "Input wire break (C-17)", P.hg17, "SAF-FW-007", "unplug E-stop / START / END while jogging; D-42 NO",
         "partial"),
    Step("HG-04", 2, "Flash erase vs IWDG, E-stop during SAVE, TX integrity (C-04)", P.hg04,
         "FW-NVM-002/003, SAF-FW-005/019", "20 SAVEs, E-stop pressed inside the erase window (OI-E-HG-04)", "partial"),
    Step("HG-14", 2, "Hang -> IWDG (C-14)", P.hg14, "SAF-FW-019", "HANG main / tick / ISR1 while jogging; .noinit record"),
    Step("HG-16", 2, "ALM / PEND levels and start-block (C-16)", P.hg16, "FW-SW-004, SAF-FW-026",
         "PSU off -> ALM, start refused; levels by DMM", "partial"),
    Step("HG-24", 2, "ALM reset (C-16, D-41)", P.hg24, "SYS-009, D-28", "PSU power cycle; ENA toggle", "manual"),
    Step("IMG-DWT", 2, "PO flashes HW_MEAS_DWT; image verified", image_step("meas_dwt"), "NFR-007", image="meas_dwt"),
    Step("HG-05", 2, "HX711 on silicon (C-05)", P.hg05, "FW-AFE-001/004, FW-TIM-001",
         "DOUT stamps vs DATA t_us, rate, reinit; DWT 19/20; SCK edges (J-AUX)", "partial", "meas_dwt"),
    Step("HG-18", 2, "Main-loop and ISR budgets — F2 (C-18)", P.hg18, "NFR-005/006/007, FW-SW-002",
         "DWT sections 0…22 under 50 kHz + 80 Hz + 20 cmd/s and real E-stop / limit / PAUSE events", "partial",
         "meas_dwt"),
    Step("HG-27", 2, "Command response sample (NFR-008)", P.hg27, "NFR-008", "1000 commands under streaming; NVM ops",
         image="meas_dwt"),
    Step("IMG-MEAS2", 2, "PO flashes HW_MEAS again; image verified", image_step("meas")),
    Step("HG-21", 2, "DRV_PWR sense (optional, D-41)", P.hg21, "(SAF-FW-024, FW-SW-005)", auto="manual"),
    Step("HG-22", 2, "K1_WELDED (N/A, D-41)", P.hg22, "(SAF-FW-025)", auto="manual"),
    # phase 3 — buffer board
    Step("HG-23", 3, "Buffer board SN74ACT244 (C-22)", P.hg23, "SYS-011, SYS-009", "inspection, I_LED, HG-07 / HG-08 repeat",
         "partial"),
    # phase 4 — load
    Step("GATE-LOAD", 4, "SYS-009 gate: prerequisites before the first load", gate_load, "SYS-009"),
    Step("HG-12", 4, "FW load-limit reaction (C-12)", P.hg12, "SAF-FW-002/008",
         "spring; threshold above the present raw; MT-4 deciding DOUT -> last PUL; MT-3 RESET mode"),
    Step("HG-15", 4, "Reset under load (C-15, D-33 e)", P.hg15, "SAF-FW-018",
         "≥ 98 N preload; NRST / Nucleo power / IWDG; dial; MT-2 = 0", "partial"),
    Step("HG-25", 4, "Speed envelope (SYS-004)", P.hg25, "SYS-004", "0.01 mm/s dial, 10 mm/s caliper, 30 mm/s, re-home, travel",
         "partial"),
    Step("HG-26", 4, "Home repeatability (FW-HOM-003)", P.hg26, "FW-HOM-003", "10 homing cycles, dial", "partial"),
    # phase 5 — release image
    Step("IMG-REL", 5, "PO flashes the release image", image_step("release"), "SYS-009", image="release"),
    Step("HG-30", 5, "Release-image confirmation (+ HG-32 repeat, HG-31)", P.hg30, "SYS-009 (R-4)",
         "GET_INFO / NOT_IN_BUILD; E-stop, limit, load limit, STOP / HALT / PAUSE, HOME, soak, loop_max_us", "partial",
         "release"),
]


def select_steps(ctx: P.Ctx, only: str | None, from_: str | None) -> list[Step]:
    """--only / --from selection with the steps that can never be skipped (FWR-15): the identity block first (S-00, or
    S-ID when S-00 already completed in this session); BENCH-ENTRY directly before and BENCH-EXIT directly after HG-10a;
    GATE-LOAD before the first phase-4 item unless it already passed in this session (target)."""
    steps = list(PLAN)
    if only:
        want = {x.strip() for x in only.split(",") if x.strip()}
        unknown = want - {s.id for s in PLAN} - {S_ID.id}
        if unknown:
            raise ValueError(f"--only: unknown step id(s) {sorted(unknown)}")
        steps = [s for s in PLAN if s.id in want]
    if from_:
        ids = [s.id for s in steps]
        if from_ not in ids:
            raise ValueError(f"--from {from_!r} is not in the selected plan")
        steps = steps[ids.index(from_):]
    by_id = {s.id: s for s in PLAN}
    ids = [s.id for s in steps]
    if "HG-10a" in ids:
        k = ids.index("HG-10a")
        if k == 0 or ids[k - 1] != "BENCH-ENTRY":
            steps.insert(k, by_id["BENCH-ENTRY"])
            k += 1
        ids = [s.id for s in steps]
        if "BENCH-EXIT" not in ids[k + 1:]:
            steps.insert(k + 1, by_id["BENCH-EXIT"])
        ids = [s.id for s in steps]
        # no duplicate entry / exit elsewhere
        steps = [s for i, s in enumerate(steps) if s.id not in ("BENCH-ENTRY", "BENCH-EXIT") or
                 abs(i - ids.index("HG-10a")) == 1]
    ids = [s.id for s in steps]
    p4 = [i for i, s in enumerate(steps) if s.phase == 4 and s.id != "GATE-LOAD"]
    if p4 and "GATE-LOAD" not in ids[:p4[0]] and not ctx.result_ok("GATE-LOAD"):
        steps.insert(p4[0], by_id["GATE-LOAD"])
    steps = [s for s in steps if s.id != "S-00"]
    first = by_id["S-00"] if not ctx.result_ok("S-00") else S_ID
    return [first] + steps


def default_delivery() -> Path:
    """Folder holding the kits named in the approval file: frozen = the folder above the gate kit that holds the
    runner (…/GateKit_FW<rel>/BirdBendStand-GateRunner/…exe → …); source run = 04_Delivery of the repository."""
    if gate_res.FROZEN:
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.name.startswith("GateKit_"):
                return parent.parent
        return exe.parent.parent
    return gate_res.TOOLS.parents[1] / "04_Delivery"


def plan_text() -> str:
    out = []
    for s in PLAN:
        out.append(f"{s.phase}  {s.id:<12} [{s.auto:<7}] {s.title}")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--twin", action="store_true", help="dry run against the FW twin (HW_MEAS model)")
    g.add_argument("--port", help="COM port named by the PO (target session; needs --approved)")
    ap.add_argument("--approval", type=Path, help="PO approval file (D-57 b, generated by make_approval.py)")
    ap.add_argument("--approved", help=argparse.SUPPRESS)          # removed (D-57): refused with a hint
    ap.add_argument("--delivery", type=Path, help="folder that holds the kits named in the approval file "
                                                  "(default: the folder above the gate kit / 04_Delivery)")
    ap.add_argument("--board-uid", help="optional cross-check: must equal the approval file's board UID")
    ap.add_argument("--twin-exe", type=Path, help="FW twin binary for --twin (default: bundled / built per run)")
    ap.add_argument("--no-export", action="store_true", help="do not write the results zip at the end")
    ap.add_argument("--out", type=Path, help="session directory (results, report)")
    ap.add_argument("--only", help="comma-separated step ids")
    ap.add_argument("--from", dest="from_", help="start at this step id (resume)")
    ap.add_argument("--quick", action="store_true", help="reduced trial counts (smoke; never evidence)")
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--buffer-fitted", action="store_true")
    ap.add_argument("--power-sense-fitted", action="store_true")
    ap.add_argument("--counts-per-n", type=float, help="HX711 counts per newton (default nominal 3285)")
    ap.add_argument("--accepted-open", action="append", default=[], metavar="HG-xx:REF",
                    help="HG item accepted open under a §6.6 residual-risk decision (reference recorded)")
    ap.add_argument("--list", action="store_true", help="print the ordered plan and exit")
    ap.add_argument("--report", type=Path, help="re-render the report of a session directory and exit")
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")      # Windows consoles (cp1252): µ, ≥, …
        except (AttributeError, ValueError):
            pass

    if a.list:
        print(plan_text())
        return 0
    if a.report:
        print(hil_report.write(a.report))
        return 0
    if not a.twin and not a.port:
        ap.error("choose --twin (dry run) or --port with --approval <file> (PO-approved board session)")
    mode = "twin" if a.twin else "target"
    try:
        validate_selection(a.only, a.from_)                  # O-3: before any port is opened
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    approval = None
    accepted: dict[str, str] = {}
    if mode == "target":
        # D-57 b: the approval file is the only source of the D-06 approval, the board UID and the accepted-open items
        if a.approved:
            print("REFUSED: --approved REF is no longer accepted (D-57): use --approval <file> generated by the "
                  "Orchestrator (make_approval.py)", file=sys.stderr)
            return 2
        if a.accepted_open:
            print("REFUSED: accepted-open items come only from the approval file (D-57 b)", file=sys.stderr)
            return 2
        if a.board_uid is not None and not hl.UID_RE.match(a.board_uid.strip()):
            print("REFUSED: --board-uid must be 24 hex digits (FWR-15)", file=sys.stderr)
            return 2
        if not a.approval:
            print("REFUSED: --approval <file> is required for a port session (D-06 / D-57 b) — no port opened",
                  file=sys.stderr)
            return 2
        delivery = a.delivery or default_delivery()
        try:
            approval = ga.check(a.approval, board_uid=a.board_uid, delivery=delivery)
        except InterlockError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        accepted = dict(approval.accepted_open)
        # C-GR-1: a board session uses only approved options - a non-default command-line option must be pinned by
        # the approval file with the same value; pinned options apply even when not typed
        cli = {"quick": a.quick, "seed": a.seed, "counts_per_n": a.counts_per_n, "buffer_fitted": a.buffer_fitted,
               "power_sense_fitted": a.power_sense_fitted}
        for k, v in cli.items():
            if v != ga.DEFAULT_OPTIONS[k] and approval.options.get(k) != v:
                print(f"REFUSED: option --{k.replace('_', '-')} is not pinned by the approval file (C-GR-1): a board "
                      "session runs only with approved options", file=sys.stderr)
                return 2
        eff = dict(ga.DEFAULT_OPTIONS, **approval.options)
        a.quick, a.seed, a.counts_per_n = eff["quick"], eff["seed"], eff["counts_per_n"]
        a.buffer_fitted, a.power_sense_fitted = eff["buffer_fitted"], eff["power_sense_fitted"]
    else:
        for x in a.accepted_open:
            item, _, ref = x.partition(":")
            if not ref:
                print(f"REFUSED: --accepted-open {x!r}: expected HG-xx:REF", file=sys.stderr)
                return 2
            accepted[item.strip()] = ref.strip()
    out = a.out or (Path.cwd() / "hil_sessions" / f"{_dt.date.today():%Y%m%d}_{mode}")
    out.mkdir(parents=True, exist_ok=True)
    meta_f = out / "session.json"
    meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
    board_uid = approval.board_uid if approval is not None else None
    approval_ref = approval.reference if approval is not None else "— (twin, D-06)"
    approval_sha = approval.body_sha256 if approval is not None else None
    # resume (--from / --only) only into the same mode, board and approval file; a plain run starts a new session
    resume = (bool(a.from_ or a.only) and bool(meta.get("session_id")) and meta.get("mode") == mode
              and meta.get("board_uid") == board_uid and meta.get("approval") == approval_ref
              and meta.get("approval_sha256") == approval_sha)
    if resume:
        session = {"session_id": meta["session_id"], "mode": mode, "board_uid": board_uid,
                   "images": dict(meta.get("images", {}))}
    else:
        if mode == "target" and any((out / "results").glob("*.json")):
            print(f"REFUSED: {out} holds results of another session (mode / board / approval differ) — use a fresh "
                  "--out for a board session (FWR-13)", file=sys.stderr)
            return 2
        session = P.new_session(mode, board_uid)
        meta = {}

    cfg = dict(P.DEFAULT_CFG)
    if a.quick:
        cfg.update(P.QUICK)
        cfg["quick"] = True
    cfg["buffer_fitted"] = a.buffer_fitted
    cfg["power_sense_fitted"] = a.power_sense_fitted
    if a.counts_per_n:
        cfg["counts_per_n"] = a.counts_per_n
    cfg["board_uid"] = board_uid
    cfg["accepted_open"] = accepted
    if approval is not None:
        cfg["approved_builds"] = approval.builds()
        cfg["evidence"] = dict(approval.evidence)
        cfg["kit"] = dict(approval.kit)
        (out / "approval.json").write_text(json.dumps(approval.doc, indent=1, ensure_ascii=False), encoding="utf-8")
    runner = gate_res.runner_identity()
    (out / "runner.json").write_text(json.dumps({
        "runner": runner, "python": sys.version.split()[0], "os": platform.platform(), "frozen": gate_res.FROZEN,
        "argv": list(argv) if argv is not None else sys.argv[1:],
        "kit": ({k: {"path": v.get("path"), "sha256": v.get("sha256"), "build": v.get("build")}
                 for k, v in approval.kit.get("images", {}).items()} if approval is not None else None),
        "kit_hashes_verified_in": str(a.delivery or default_delivery()) if approval is not None else None,
        "started_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}, indent=1), encoding="utf-8")

    if mode == "twin":
        exe = a.twin_exe or gate_res.twin_exe_default()
        if exe is None and not os.environ.get("BEND_TWIN_BUILD_DIR"):
            os.environ["BEND_TWIN_BUILD_DIR"] = str(out / "twin_build")      # standing data rule: inside <out>
        tr = LoggedTransport(TwinTransport(out / "twin_run", image="meas", exe=exe, seed=1), out / "raw" / "frames.bin")
        link = Link(tr)
        op = TwinOperator(link, seed=a.seed)
    else:
        op = ConsoleOperator(pictures=gate_res.pictures(), plain=gate_res.plain_prompts())
        try:
            tr = LoggedTransport(SerialTransport(a.port, approval, confirm=op.confirm_port), out / "raw" / "frames.bin")
        except InterlockError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        link = Link(tr, step_ms=0.5)
    op.journal = out / "prompts.jsonl"
    ctx = P.Ctx(link, op, cfg, out, seed=a.seed, session=session)

    try:
        steps = select_steps(ctx, a.only, a.from_)
    except ValueError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        tr.close()
        return 2

    meta.update({"session_id": session["session_id"], "images": session["images"],
                 "mode": mode, "date": time.strftime("%Y-%m-%d %H:%M"), "approval": approval_ref,
                 "approval_sha256": approval_sha, "runner": runner,
                 "options": {"quick": a.quick, "seed": a.seed, "counts_per_n": a.counts_per_n,
                             "buffer_fitted": a.buffer_fitted, "power_sense_fitted": a.power_sense_fitted},
                 "options_pinned": sorted(approval.options) if approval is not None else [],
                 "port": a.port or "— (twin)", "board_uid": board_uid, "icd": rc.ICD_VERSION,
                 "python": sys.version.split()[0], "host": platform.node(), "seed": a.seed, "quick": a.quick,
                 "order": [s.id for s in PLAN],
                 "twin_exe": str(getattr(tr, "exe", "")) if mode == "twin" else "–"})
    meta_f.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    rc_ = 0
    try:
        for s in steps:
            print(f"--- {s.id}: {s.title}", flush=True)
            t0 = time.time()
            if s.id == "HG-10a" and not ctx.result_ok("BENCH-ENTRY"):
                it = ctx.begin(s.id, s.title, s.req, s.method)
                it.error = "REFUSED: bench entry (§6.8 P-1…P-5) has not completed in this session"
                ctx.end()
                print(f"    {it.error}", flush=True)
                continue
            if s.phase == 4 and s.id != "GATE-LOAD" and not ctx.twin:
                g_ = ctx.result_of("GATE-LOAD")
                if not g_ or g_.get("error") or g_["verdict"] != hb.PASS:
                    it = ctx.begin(s.id, s.title, s.req, s.method)
                    it.error = "REFUSED: SYS-009 load gate not passed in this session"
                    ctx.end()
                    print(f"    {it.error}", flush=True)
                    continue
            why0 = unidentified_reason(ctx, s.id, before=True)
            if why0:
                it = ctx.begin(s.id, s.title, s.req, s.method)
                it.error = f"REFUSED: {why0}"
                ctx.end()
                print(f"    {it.error}", flush=True)
                print(f"REFUSED: {why0} — session stopped. {MANUAL_RESTORE}", file=sys.stderr)
                rc_ = 2
                break
            it = P.run_item(ctx, s.id, s.title, s.fn, s.req, s.method)
            print(f"    {it.verdict}  ({time.time() - t0:.1f} s)" + (f"  {it.error}" if it.error else ""), flush=True)
            meta_f.write_text(json.dumps(meta, indent=1), encoding="utf-8")        # images of this session (resume)
            why = stop_reason(s.id, it) or unidentified_reason(ctx, s.id, before=False)
            if why:
                if s.id == "BENCH-ENTRY" and ctx.identified:
                    # the sense bypass may be (partly) fitted: restore it (P-8, no motion) before stopping
                    ex = next(x for x in PLAN if x.id == "BENCH-EXIT")
                    print(f"--- {ex.id}: {ex.title} (restore before stopping)", flush=True)
                    P.run_item(ctx, ex.id, ex.title, ex.fn, ex.req, ex.method)
                print(f"REFUSED: {why} — session stopped before any further motion"
                      + ("" if ctx.identified or ctx.twin else f". {MANUAL_RESTORE}"), file=sys.stderr)
                rc_ = 2
                break
    except (KeyboardInterrupt, OperatorAbort) as e:
        if ctx.identified:
            print(f"ABORT ({type(e).__name__}) — sending HALT", file=sys.stderr)
            try:
                link.cmd("HALT", timeout_ms=500)
            except Exception:  # noqa: BLE001
                pass
        else:
            print(f"ABORT ({type(e).__name__}) — device not identified: nothing sent (C-R3)", file=sys.stderr)
        rc_ = 3
    finally:
        try:
            meta["fw_build"] = ctx.result_of("S-00")["data"]["info"]["build"] if ctx.result_of("S-00") else "–"
        except Exception:  # noqa: BLE001
            pass
        meta_f.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        rep = hil_report.write(out)
        print(f"report: {rep}")
        tr.close()
        if not a.no_export:
            try:
                z = gate_export.export(out)
                zsha = gate_res.sha256_file(z)
                (out / "zip_sha256.txt").write_text(f"{z.name}  SHA-256 {zsha}\n", encoding="utf-8")
                bar = "=" * 100
                print(f"results zip: {z}  — send this file to the product owner\n{bar}\n"
                      "  SEND THIS LINE TO THE PRODUCT OWNER NOW, SEPARATELY FROM THE ZIP (e.g. in the message text):\n\n"
                      f"      {z.name}   SHA-256   {zsha}\n\n{bar}")
            except Exception as e:  # noqa: BLE001
                print(f"results zip NOT written: {e}", file=sys.stderr)
    return rc_


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""HG1 hardware-gate session runner (FW_test_plan v0.4.1 §6; order §6.7; bench safety procedure §6.8).

Owner: Validator E (00_System/tools/hil, the Integrator reviews). Runbook: HW_GATE_RUNBOOK.md (same folder).

  Twin dry run (no hardware, D-06 respected — the only mode allowed before the PO's approval):
    .venv\\Scripts\\python 00_System\\tools\\hil\\hil_session.py --twin --out <dir> [--quick] [--only HG-09,HG-14]

  Board session (ONLY after the PO approved the gate and the Orchestrator recorded the reference):
    .venv\\Scripts\\python 00_System\\tools\\hil\\hil_session.py --port COM7 --approved D-06-GATE-20261012 \\
        --board-uid <96-bit UID hex> --out <dir> [--from HG-28] [--only ...]

  Other:  --list (the ordered plan) · --report <dir> (re-render the markdown report)

D-06 interlock (hil_link.open_serial): no serial port is opened without --approved D-06-GATE-YYYYMMDD[-TAG] that is
dated, not in the future, ≤ 7 days old and recorded in specs/DECISIONS.md or STATUS.md, an explicit --port, and the
operator re-typing the port name; with --board-uid the GET_INFO UID must match. --twin and --port are exclusive.
Flashing is never done by this tool: image steps ask the PO to flash and then verify GET_INFO / DIAG_MEAS INFO.
Results: <out>/results/<item>.json, <out>/session.json, <out>/HG_report_<mode>.md.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import hil_budget as hb  # noqa: E402
import hil_procs as P  # noqa: E402
import hil_report  # noqa: E402
import ref_codec as rc  # noqa: E402
from hil_link import InterlockError, Link, SerialTransport, TwinTransport  # noqa: E402
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
    p = op.ask_text("PERSONS", "Operator and observer names", nominal="(dry run)")
    ctx.data("persons", p.value)
    c = op.ask_text("COMMIT", "Git commit of the three images (release / HW_MEAS / HW_MEAS_DWT, one commit)",
                    nominal="(dry run)")
    ctx.data("images_commit", c.value)
    inf = ctx.L.info()
    ctx.data("info", inf)
    ctx.check(hb.check_eq("param dict hash", inf["param_dict_hash"], P.DICT_HASH, "gen_params (R-HIL-01)"))
    ctx.check(hb.check_eq("protocol 1.0 / payload 1", (inf["proto_major"], inf["proto_minor"], inf["payload_version"]),
                          (1, 0, 1)))
    want_uid = ctx.cfg.get("board_uid")
    if want_uid and not ctx.twin:
        if inf["uid"].upper() != want_uid.upper():
            raise P.BenchRefused(f"board UID {inf['uid']} != PO-named {want_uid} — wrong board, session stopped")
        ctx.check(hb.check_eq("board UID (PO-named)", inf["uid"], want_uid.upper()))
    dip = op.ask_yes_no("DIP", "Driver DIP target setting applied with the driver unpowered (D-27, wiring §10)?",
                        nominal=True)
    ctx.judged(hb.check_true("DIP applied before the first motion", dip.value, "D-27"), dip)
    ctx.check(hb.info("TC-SYS-009-02 / check_meas_build", "see HG-29 f"))


def image_step(image: str) -> Callable[[P.Ctx], None]:
    def fn(ctx: P.Ctx) -> None:
        names = {"meas": "nucleo_f446re_meas (HW_MEAS)", "meas_dwt": "nucleo_f446re_meas_dwt (HW_MEAS_DWT)",
                 "release": "nucleo_f446re (release)"}
        if ctx.twin:
            ctx.L.tr.reflash(image)
            ctx.note(f"twin: engine restarted with hw_meas={image != 'release'} (stand-in for flashing {names[image]})")
        else:
            ctx.L.tr.close_port()
            ctx.op.instruct("FLASH", f"PO: flash {names[image]} (firmware.bin of the recorded commit) with "
                            "STM32CubeProgrammer over the ST-LINK; the session closed the COM port. Board resets.")
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
    ap.add_argument("--approved", help="PO approval reference D-06-GATE-YYYYMMDD[-TAG] (recorded by the Orchestrator)")
    ap.add_argument("--board-uid", help="96-bit UID (hex) of the PO-named board; GET_INFO must match")
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
        ap.error("choose --twin (dry run) or --port with --approved (PO-approved board session)")
    mode = "twin" if a.twin else "target"
    out = a.out or (Path.cwd() / "hil_sessions" / f"{_dt.date.today():%Y%m%d}_{mode}")
    out.mkdir(parents=True, exist_ok=True)

    cfg = dict(P.DEFAULT_CFG)
    if a.quick:
        cfg.update(P.QUICK)
        cfg["quick"] = True
    cfg["buffer_fitted"] = a.buffer_fitted
    cfg["power_sense_fitted"] = a.power_sense_fitted
    if a.counts_per_n:
        cfg["counts_per_n"] = a.counts_per_n
    cfg["board_uid"] = a.board_uid
    cfg["accepted_open"] = dict(x.split(":", 1) for x in a.accepted_open if ":" in x)

    if mode == "twin":
        tr = TwinTransport(out / "twin_run", image="meas", seed=1)
        link = Link(tr)
        op = TwinOperator(link, seed=a.seed)
    else:
        op = ConsoleOperator()
        try:
            tr = SerialTransport(a.port, a.approved, confirm=op.confirm_port)
        except InterlockError as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            return 2
        link = Link(tr, step_ms=0.5)
    ctx = P.Ctx(link, op, cfg, out, seed=a.seed)

    steps = PLAN
    if a.only:
        want = {s.strip() for s in a.only.split(",")}
        steps = [s for s in PLAN if s.id in want]
    if a.from_:
        ids = [s.id for s in steps]
        steps = steps[ids.index(a.from_):]

    meta_f = out / "session.json"
    meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
    meta.update({"mode": mode, "date": time.strftime("%Y-%m-%d %H:%M"), "approval": a.approved or "— (twin, D-06)",
                 "port": a.port or "— (twin)", "board_uid": a.board_uid or "–", "icd": rc.ICD_VERSION,
                 "python": sys.version.split()[0], "host": platform.node(), "seed": a.seed, "quick": a.quick,
                 "order": [s.id for s in PLAN],
                 "twin_exe": str(getattr(tr, "exe", "")) if mode == "twin" else "–"})
    meta_f.write_text(json.dumps(meta, indent=1), encoding="utf-8")

    rc_ = 0
    try:
        for s in steps:
            print(f"--- {s.id}: {s.title}", flush=True)
            t0 = time.time()
            if s.id == "HG-10a":
                be = ctx.result_of("BENCH-ENTRY")
                if be and be.get("error"):
                    it = ctx.begin(s.id, s.title, s.req, s.method)
                    it.error = "REFUSED: bench entry (§6.8) did not complete"
                    ctx.end()
                    continue
            if s.phase == 4 and s.id != "GATE-LOAD" and not ctx.twin:
                g_ = ctx.result_of("GATE-LOAD")
                if not g_ or g_.get("error") or g_["verdict"] != hb.PASS:
                    it = ctx.begin(s.id, s.title, s.req, s.method)
                    it.error = "REFUSED: SYS-009 load gate not passed"
                    ctx.end()
                    continue
            it = P.run_item(ctx, s.id, s.title, s.fn, s.req, s.method)
            print(f"    {it.verdict}  ({time.time() - t0:.1f} s)" + (f"  {it.error}" if it.error else ""), flush=True)
    except (KeyboardInterrupt, OperatorAbort) as e:
        print(f"ABORT ({type(e).__name__}) — sending HALT", file=sys.stderr)
        try:
            link.cmd("HALT", timeout_ms=500)
        except Exception:  # noqa: BLE001
            pass
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
    return rc_


if __name__ == "__main__":
    sys.exit(main())

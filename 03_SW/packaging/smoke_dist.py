"""Smoke test of a built distribution folder (``03_SW/dist/BirdBendStand-<ver>/``) — no hardware (D-06).

Every run gets a private data folder (``BEND_STAND_DATA_DIR``), a private ``gui.ini`` (``BEND_STAND_GUI_SETTINGS``),
the hotkey off (``BEND_STAND_HOTKEY=off``) and the frozen-exe **D-06 guard** (``BEND_STAND_D06_GUARD=1``, see
``rthook_bend_stand.py``): any attempt to open a COM port raises ``HardwareAccessForbidden``. GUI runs use
``QT_QPA_PLATFORM=offscreen`` and ``BEND_STAND_GUI_QUIT_AFTER_MS`` unless ``--native-gui`` is given.

Checks (all must pass, exit code 0):

=====  ==========================================================================================================
S1     ``BirdBendStand-cli.exe --version`` → rc 0, prints the stamped version (``<base>+g<hash>``)
S2     ``BirdBendStand-cli.exe --headless --sim`` → rc 0, connected, link CONNECTED, DATA frames > 0
S3     ``BirdBendStand.exe --headless --sim`` (windowed exe) → rc 0, same statistics (log file or pipe)
S4     ``BirdBendStand.exe --sim`` GUI, auto-quit → rc 0, exit summary: link CONNECTED, stream on, DATA > 0
S5     ``BirdBendStand.exe`` GUI without endpoint (starts disconnected), auto-quit → rc 0, link not connected
S6     D-06 guard: ``BirdBendStand-cli.exe --headless --port COM250`` → rc ≠ 0 and the guard message (the guarded
       exe never calls the OS to open the port)
S7     layout: no test / build-tool packages in ``_internal`` (pytest, PyInstaller, yaml), Qt ``qwindows`` platform
       plugin present, version dist-info present, GUI icon resource ``bend_stand/gui/resources/BirdBendStand.ico``;
       operator docs in ``docs/`` (USER_MANUAL / QUICK_REFERENCE as .md + .html, every referenced screenshot)
S9     windowed exe started without console handles (``DETACHED_PROCESS``): stdio in ``logs/BirdBendStand.log``,
       logging records in ``logs/bend_stand.log`` only — the GUI exit line exactly once (SWR-14, OI-F-RV-04);
       S2 / S3 also require ``bend_stand.log`` with the start line and the incident lines (link, FW BOOT)
S8     offline report: ``BirdBendStand-cli.exe --headless --sim --record --recordings <tmp>`` writes a recording,
       ``BirdBendStand-cli.exe report <recording> --out <tmp>`` → rc 0, ``report.json`` (bird.bend.report, rows > 0)
       + ``report.html``; a missing folder → rc 2
=====  ==========================================================================================================

Usage: ``.venv\\Scripts\\python 03_SW\\packaging\\smoke_dist.py --dist 03_SW\\dist\\BirdBendStand-<ver>
[--gui-ms 8000] [--native-gui] [--json out.json]``

Implements: SW-PLT-001 (fresh-install start check, DM-02 preparation), SYS-008 (``--sim`` in the frozen app), D-06
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

GUI_EXE = "BirdBendStand.exe"
CLI_EXE = "BirdBendStand-cli.exe"
LOG_REL = Path("logs") / "BirdBendStand.log"          # stdio of the windowed exe (runtime hook)
APP_LOG_REL = Path("logs") / "bend_stand.log"         # application log (core.logfile, SWR-14)
DOC_FILES = ("USER_MANUAL.md", "QUICK_REFERENCE.md")


@dataclass
class Result:
    name: str
    ok: bool
    rc: int | None
    seconds: float
    detail: str
    extra: dict[str, object] = field(default_factory=dict)


class Runner:
    def __init__(self, dist: Path, work: Path, gui_ms: int, native_gui: bool) -> None:
        self.dist, self.work, self.gui_ms, self.native_gui = dist, work, gui_ms, native_gui
        self.n = 0

    def env(self, gui: bool) -> tuple[dict[str, str], Path]:
        self.n += 1
        data = self.work / f"appdata-{self.n}"
        e = dict(os.environ)
        e.update(BEND_STAND_DATA_DIR=str(data), BEND_STAND_GUI_SETTINGS=str(data / "gui.ini"),
                 BEND_STAND_HOTKEY="off", BEND_STAND_D06_GUARD="1")
        e.pop("PYTHONPATH", None)
        e.pop("PYTHONHOME", None)
        if gui:
            e["BEND_STAND_GUI_QUIT_AFTER_MS"] = str(self.gui_ms)
            if not self.native_gui:
                e["QT_QPA_PLATFORM"] = "offscreen"
        return e, data

    def run(self, exe: str, args: list[str], gui: bool = False, timeout: float = 120.0,
            ) -> tuple[int, str, float, float | None, Path]:
        """(rc, stdout+stderr+log file, wall s, s until a 'connected:' line or None, data dir)."""
        env, data = self.env(gui)
        t0 = time.perf_counter()
        p = subprocess.Popen([str(self.dist / exe), *args], cwd=str(self.work), env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                             errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        t_conn: float | None = None
        lines: list[str] = []
        try:
            assert p.stdout is not None
            for line in p.stdout:                    # ends at EOF (process exit)
                if t_conn is None and line.startswith("connected:"):
                    t_conn = time.perf_counter() - t0
                lines.append(line)
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()                                 # only the process this runner started
            p.wait(timeout=30)
            rc = -999
        wall = time.perf_counter() - t0
        out = "".join(lines)
        for rel in (LOG_REL, APP_LOG_REL):
            log = data / rel
            if log.is_file():
                out += f"\n[{rel.name}]\n" + log.read_text(encoding="utf-8", errors="replace")
        return rc, out, wall, t_conn, data


def _stat(out: str, key: str) -> int | None:
    m = re.search(rf"{key} (\d+)", out)
    return int(m.group(1)) if m else None


def _tail(out: str, n: int = 12) -> str:
    return "\n".join(out.strip().splitlines()[-n:])


def check_version(r: Runner, expect: str | None) -> Result:
    rc, out, wall, _, _ = r.run(CLI_EXE, ["--version"])
    m = re.search(r"bend_stand (\S+)", out)
    got = m.group(1) if m else None
    ok = rc == 0 and got is not None and "+" in got and (expect is None or got == expect)
    return Result("S1 cli --version", ok, rc, wall, out.strip().splitlines()[0] if out.strip() else "(no output)",
                  {"version": got, "expected": expect})


def _app_log(data_dir: Path) -> str:
    p = data_dir / APP_LOG_REL
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def check_headless(r: Runner, exe: str, name: str, duration: float) -> Result:
    rc, out, wall, t_conn, data_dir = r.run(exe, ["--headless", "--sim", "--duration", f"{duration:g}"])
    data = _stat(out, "data")
    app_log = _app_log(data_dir)
    log_ok = "log start" in app_log and "link CONNECTED sim" in app_log and "FW EVENT BOOT" in app_log
    ok = (rc == 0 and "connected: sim" in out and "link state CONNECTED" in out and "stream on" in out
          and (data or 0) > 0 and log_ok)
    detail = _tail(out, 4) + ("" if log_ok else "\nbend_stand.log without the expected incident lines")
    return Result(name, ok, rc, wall, detail, {"data_frames": data, "t_connected_s": t_conn,
                                               "app_log_lines": len(app_log.splitlines())})


def check_windowed_log_once(r: Runner) -> Result:
    """S9 (SWR-14, OI-F-RV-04): the windowed exe started **without** console handles (as from Explorer / a shortcut):
    stdio goes to BirdBendStand.log (runtime hook), logging records to bend_stand.log only — never to both."""
    env, data_dir = r.env(gui=True)
    t0 = time.perf_counter()
    p = subprocess.Popen([str(r.dist / GUI_EXE), "--sim", "--log-level", "INFO"], cwd=str(r.work), env=env,
                         close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    try:
        rc = p.wait(timeout=120)
    except subprocess.TimeoutExpired:
        p.kill()                                     # only the process started here
        p.wait(timeout=30)
        rc = -999
    wall = time.perf_counter() - t0
    app_log = _app_log(data_dir)
    stdio = (data_dir / LOG_REL).read_text(encoding="utf-8", errors="replace") if (data_dir / LOG_REL).is_file() else ""
    n_app, n_stdio = app_log.count("GUI exit rc=0"), stdio.count("GUI exit rc=0")
    ok = (rc == 0 and "===== start" in stdio and n_app == 1 and n_stdio == 0 and "link CONNECTED sim" in app_log
          and "INFO" in app_log)
    return Result("S9 windowed exe: log written once", ok, rc, wall,
                  f"bend_stand.log: {len(app_log.splitlines())} lines, 'GUI exit' x{n_app}; "
                  f"BirdBendStand.log (stdio): {len(stdio.splitlines())} lines, 'GUI exit' x{n_stdio}",
                  {"app_log": str(data_dir / APP_LOG_REL)})


def check_gui(r: Runner, args: list[str], name: str, expect_connected: bool) -> Result:
    rc, out, wall, _, data_dir = r.run(GUI_EXE, [*args, "--log-level", "INFO"], gui=True)
    m = re.search(r"GUI exit rc=(-?\d+); (.*)", out)
    summary = m.group(2) if m else ""
    data = _stat(summary, "data")
    if expect_connected:
        ok = (rc == 0 and m is not None and "link CONNECTED" in summary and "stream on" in summary
              and (data or 0) > 0)
    else:
        ok = rc == 0 and m is not None and "link CONNECTED" not in summary
    return Result(name, ok, rc, wall, summary or _tail(out), {"data_frames": data, "gui_ms": r.gui_ms,
                                                            "startup_overhead_s": round(wall - r.gui_ms / 1000, 2),
                                                            "log_file": str(data_dir / LOG_REL)})


def check_guard(r: Runner) -> Result:
    rc, out, wall, _, _ = r.run(CLI_EXE, ["--headless", "--port", "COM250", "--duration", "1"])
    ok = rc != 0 and "HardwareAccessForbidden" in out and "BEND_STAND_D06_GUARD" in out
    return Result("S6 D-06 guard (COM250 refused)", ok, rc, wall, _tail(out, 3))


def docs_problems(dist: Path) -> list[str]:
    """Operator docs installed under ``docs/`` (OI-UM-03): both manuals as .md and .html, every image the
    manuals reference present, and the HTML pages link to their screenshots."""
    docs = dist / "docs"
    out = []
    for name in DOC_FILES:
        for f in (docs / name, docs / (name[:-3] + ".html")):
            if not f.is_file():
                out.append(f"docs/{f.name} missing")
        if (docs / name).is_file():
            refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", (docs / name).read_text(encoding="utf-8"))
            out += [f"docs/{ref} missing" for ref in refs if not (docs / ref).is_file()]
    page = docs / "USER_MANUAL.html"
    if page.is_file() and "<img src=\"img/" not in page.read_text(encoding="utf-8"):
        out.append("USER_MANUAL.html shows no screenshots")
    return out


def check_report(r: Runner, duration: float) -> Result:
    """S8: a short ``--headless --sim --record`` recording, then ``BirdBendStand-cli.exe report <folder> --out``."""
    rec_root = r.work / "recordings"
    rc1, out1, wall1, _, _ = r.run(CLI_EXE, ["--headless", "--sim", "--duration", f"{duration:g}", "--record",
                                             "--recordings", str(rec_root)])
    m = re.search(r"^recording: (.+?)  rows (\d+)", out1, re.M)
    if rc1 != 0 or m is None:
        return Result("S8 cli report (offline rebuild)", False, rc1, wall1, _tail(out1, 6))
    folder, rows = Path(m.group(1)), int(m.group(2))
    out_dir = r.work / "report-out"
    rc2, out2, wall2, _, _ = r.run(CLI_EXE, ["report", str(folder), "--out", str(out_dir)])
    detail = [f"recording {folder.name}: {rows} rows"]
    ok = rc2 == 0 and (out_dir / "report.html").is_file() and (out_dir / "report.json").is_file()
    if ok:
        doc = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
        ok = doc.get("schema") == "bird.bend.report" and (doc.get("summary") or {}).get("rows", 0) > 0
        detail.append(f"report.json schema {doc.get('schema')} rows {(doc.get('summary') or {}).get('rows')} "
                      f"sw_version {doc.get('sw_version')}; report.html "
                      f"{(out_dir / 'report.html').stat().st_size} B")
    else:
        detail.append(_tail(out2, 6))
    rc3, out3, _, _, _ = r.run(CLI_EXE, ["report", str(r.work / "no-such-recording")])
    ok = ok and rc3 == 2 and "error:" in out3
    detail.append(f"missing folder -> rc {rc3}")
    return Result("S8 cli report (offline rebuild)", ok, rc2, wall1 + wall2, "\n".join(detail),
                  {"rows": rows})


def check_layout(dist: Path) -> Result:
    internal = dist / "_internal"
    problems = []
    for bad in ("pytest", "_pytest", "PyInstaller", "yaml", "pytestqt"):
        if (internal / bad).exists():
            problems.append(f"{bad} bundled")
    if not (internal / "PySide6" / "plugins" / "platforms" / "qwindows.dll").is_file():
        problems.append("qwindows.dll missing")
    if not list(internal.glob("bend_stand-*.dist-info")):
        problems.append("bend_stand dist-info missing")
    for exe in (GUI_EXE, CLI_EXE):
        if not (dist / exe).is_file():
            problems.append(f"{exe} missing")
    gui_icon = internal / "bend_stand" / "gui" / "resources" / "BirdBendStand.ico"     # gui.resources.app_icon()
    if not gui_icon.is_file():
        problems.append("bend_stand/gui/resources/BirdBendStand.ico missing")
    elif gui_icon.read_bytes()[:4] != b"\x00\x00\x01\x00":
        problems.append("bend_stand/gui/resources/BirdBendStand.ico is not an ICO file")
    problems += docs_problems(dist)
    size = sum(p.stat().st_size for p in dist.rglob("*") if p.is_file())
    return Result("S7 layout", not problems, None, 0.0, "; ".join(problems) or "ok",
                  {"size_mib": round(size / 2**20, 1), "files": sum(1 for p in dist.rglob("*") if p.is_file())})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smoke_dist.py")
    ap.add_argument("--dist", required=True)
    ap.add_argument("--gui-ms", type=int, default=8000)
    ap.add_argument("--headless-s", type=float, default=3.0)
    ap.add_argument("--native-gui", action="store_true", help="GUI runs on the real display (window appears)")
    ap.add_argument("--json", default=None)
    ap.add_argument("--keep", action="store_true", help="keep the temporary data folders")
    ap.add_argument("--work", default=None, help="parent folder for the run folders (default: system temp)")
    args = ap.parse_args(argv)
    dist = Path(args.dist).resolve()
    expect = None
    di = list((dist / "_internal").glob("bend_stand-*.dist-info"))
    if di:
        expect = di[0].name[len("bend_stand-"):-len(".dist-info")]
    if args.work:
        Path(args.work).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="bbs-smoke-", dir=args.work))
    r = Runner(dist, work, args.gui_ms, args.native_gui)
    results = [check_layout(dist), check_version(r, expect),
               check_headless(r, CLI_EXE, "S2 cli --headless --sim", args.headless_s),
               check_headless(r, GUI_EXE, "S3 exe --headless --sim", args.headless_s),
               check_gui(r, ["--sim"], "S4 GUI --sim (auto-quit)", True),
               check_gui(r, [], "S5 GUI disconnected (auto-quit)", False),
               check_guard(r), check_report(r, args.headless_s), check_windowed_log_once(r)]
    for res in results:
        print(f"[{'PASS' if res.ok else 'FAIL'}] {res.name}: rc={res.rc} {res.seconds:.2f} s  {res.extra}")
        for line in res.detail.splitlines():
            print(f"        {line}")
    n_fail = sum(not x.ok for x in results)
    print(f"smoke: {len(results) - n_fail}/{len(results)} PASS ({dist.name})")
    if args.json:
        Path(args.json).write_text(json.dumps([res.__dict__ for res in results], indent=2, default=str),
                                   encoding="utf-8")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

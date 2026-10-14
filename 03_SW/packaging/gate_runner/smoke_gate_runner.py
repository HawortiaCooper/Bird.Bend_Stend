"""Smoke test of the frozen BirdBendStand-GateRunner folder (D-57, GR-1) — no hardware, no COM port (D-06).

Every run: cwd = a sandbox folder, TEMP / TMP / APPDATA / LOCALAPPDATA / USERPROFILE / HOME redirected into the sandbox,
``BEND_STAND_D06_GUARD=1`` (the runtime hook makes any serial open fail even if an interlock regressed), PYTHON*
removed. Afterwards nothing may exist in the sandbox outside the runs' ``--out`` folders (stray-run data rule).

=====  ==========================================================================================================
G1     ``--version`` → rc 0, "(frozen)", and the printed SHA-256 = ``gate_res.bundle_sha256`` of the repository (the
       value ``make_approval.py --runner-exe`` computes)
G2     ``--list`` → rc 0, the ordered plan (HG-01 … HG-32)
G3     ``--twin --quick --out <out>`` → rc 0, one results zip ``gate_*_twin_*.zip`` in ``<out>``
G4     ``--verify-zip <zip>`` → rc 0 "results zip intact"; a copy with one member changed → rc 1
G5     refusals before any port / session folder: ``--port COM250`` without an approval file → rc 2 "REFUSED"; with a
       forged approval file → rc 2 "REFUSED"; ``--out`` not created; the D-06 guard never reached
G6     stray-run rule: no file in the sandbox outside the ``--out`` folders
G7     layout: params.yaml / pictures.json / pictures/B*.png identical to the repository, fw_twin/fw_twin.exe present,
       no GUI / app packages (PySide6, numpy, bend_stand) bundled
=====  ==========================================================================================================

Usage: ``.venv\\Scripts\\python 03_SW\\packaging\\gate_runner\\smoke_gate_runner.py --dist <…/BirdBendStand-GateRunner>
[--work <dir>] [--json out.json] [--skip-twin]``

Implements: D-57 (gate runner smoke, GR-1), D-06
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
HIL = REPO / "00_System" / "tools" / "hil"
EXE = "BirdBendStand-GateRunner.exe"
ENV_KEYS = ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")


@dataclass
class Result:
    name: str
    ok: bool
    rc: int | None
    seconds: float
    detail: str
    extra: dict[str, object] = field(default_factory=dict)


class Sandbox:
    def __init__(self, dist: Path, root: Path) -> None:
        self.dist, self.root = dist, root
        self.cwd = root / "cwd"
        self.homes = {k: root / "env" / k for k in ENV_KEYS}
        for p in (self.cwd, *self.homes.values()):
            p.mkdir(parents=True, exist_ok=True)
        self.outs: list[Path] = []

    def out(self, name: str) -> Path:
        p = self.root / name
        self.outs.append(p)
        return p

    def run(self, args: list[str], timeout: float = 120.0) -> tuple[int, str, float]:
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        env.update({k: str(v) for k, v in self.homes.items()})
        env["BEND_STAND_D06_GUARD"] = "1"
        t0 = time.perf_counter()
        try:
            r = subprocess.run([str(self.dist / EXE), *args], cwd=str(self.cwd), env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=timeout,
                               stdin=subprocess.DEVNULL)
            rc, out = r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired as exc:            # subprocess.run kills the child it started
            rc, out = -999, f"timeout after {timeout} s: {exc}"
        return rc, out, time.perf_counter() - t0

    def stray(self) -> list[str]:
        keep = [p.resolve() for p in self.outs] + [(self.root / "input").resolve()]
        out = []
        for p in self.root.rglob("*"):
            if p.is_file() and not any(k == p.resolve() or k in p.resolve().parents for k in keep):
                out.append(p.relative_to(self.root).as_posix())
        return sorted(out)


def _tail(s: str, n: int = 6) -> str:
    return "\n".join(s.strip().splitlines()[-n:])


def repo_bundle_sha(exe: Path) -> str:
    if str(HIL) not in sys.path:
        sys.path.insert(0, str(HIL))
    import gate_res  # noqa: PLC0415

    return gate_res.bundle_sha256(exe, gate_res.bundle_root_of(exe))


def g1_version(sb: Sandbox) -> Result:
    rc, out, t = sb.run(["--version"])
    sha = repo_bundle_sha(sb.dist / EXE)
    ok = rc == 0 and "(frozen)" in out and f"sha256 {sha}" in out
    return Result("G1 --version = make_approval hash", ok, rc, t, _tail(out, 1), {"runner_sha256": sha})


def g2_list(sb: Sandbox) -> Result:
    rc, out, t = sb.run(["--list"])
    ok = rc == 0 and "HG-01" in out and "HG-3" in out
    return Result("G2 --list", ok, rc, t, f"{len(out.splitlines())} lines; first: {out.strip().splitlines()[0][:100]}"
                  if out.strip() else "(no output)")


def g3_twin(sb: Sandbox, quick: bool) -> tuple[Result, Path | None]:
    out_dir = sb.out("out-twin")
    rc, out, t = sb.run(["--twin", *(["--quick"] if quick else []), "--out", str(out_dir)], timeout=3600)
    zips = sorted(out_dir.glob("gate_*.zip")) if out_dir.is_dir() else []
    ok = rc == 0 and len(zips) == 1 and "_twin_" in zips[0].name
    size = zips[0].stat().st_size if zips else 0
    return (Result("G3 --twin --quick", ok, rc, t, _tail(out, 4) + (f"\nzip {zips[0].name} {size} B" if zips else ""),
                   {"zip_bytes": size}), zips[0] if zips else None)


def g4_verify(sb: Sandbox, zip_path: Path | None) -> Result:
    if zip_path is None:
        return Result("G4 --verify-zip", False, None, 0.0, "no results zip from G3")
    rc, out, t = sb.run(["--verify-zip", str(zip_path)])
    bad = sb.out("out-tamper") / "tampered.zip"
    bad.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as dst:
        changed = False
        for zi in src.infolist():
            data = src.read(zi.filename)
            if not changed and zi.filename.endswith(".json") and zi.filename != "manifest.json":
                data += b" "                                      # one member changed, manifest untouched
                changed = True
            dst.writestr(zi, data)
    rc2, out2, t2 = sb.run(["--verify-zip", str(bad)])
    ok = rc == 0 and "results zip intact" in out and rc2 == 1 and "PROBLEMS" in out2
    return Result("G4 --verify-zip (+ tampered copy)", ok, rc, t + t2,
                  f"{_tail(out, 1)}; tampered → rc {rc2}: {_tail(out2, 2)}")


def g5_refusals(sb: Sandbox) -> Result:
    inp = sb.root / "input"
    inp.mkdir(exist_ok=True)
    forged = inp / "approval_forged.json"
    forged.write_text(json.dumps({"schema": "bird.bend.gate_approval", "schema_version": 1,
                                  "reference": "D-06-GATE-20261014", "board_uid": "0" * 24,
                                  "body_sha256": "0" * 64}), encoding="utf-8")
    cases = [("no approval file", ["--port", "COM250", "--out", str(sb.root / "refused-1")]),
             ("forged approval", ["--approval", str(forged), "--port", "COM250", "--out", str(sb.root / "refused-2")]),
             ("--approved REF", ["--approved", "D-06-GATE-20261014", "--port", "COM250",
                                 "--out", str(sb.root / "refused-3")])]
    lines, ok, total = [], True, 0.0
    for name, args in cases:
        rc, out, t = sb.run(args)
        total += t
        out_dir = Path(args[args.index("--out") + 1])
        good = rc == 2 and "REFUSED" in out and not out_dir.exists() and "D-06: opening a hardware port" not in out
        ok = ok and good
        lines.append(f"{name}: rc {rc}, out folder {'created' if out_dir.exists() else 'not created'} — "
                     f"{_tail(out, 1)[:150]}")
    return Result("G5 refusals before any port", ok, 2, total, "\n".join(lines))


def g7_layout(dist: Path) -> Result:
    internal = dist / "_internal"
    res = internal / "resources"
    problems = []
    if (res / "params.yaml").read_bytes() != (REPO / "00_System" / "specs" / "params.yaml").read_bytes():
        problems.append("params.yaml differs from 00_System/specs/params.yaml")
    if (res / "pictures.json").read_bytes() != (HIL / "resources" / "pictures.json").read_bytes():
        problems.append("pictures.json differs")
    want = {p.name: p.read_bytes() for p in (REPO / "01_HW" / "diagrams" / "bench").glob("B*.png")}
    have = {p.name: p.read_bytes() for p in (res / "pictures").glob("*")}
    if want != have:
        problems.append(f"pictures differ: missing {sorted(set(want) - set(have))}, extra {sorted(set(have) - set(want))}")
    if not (internal / "fw_twin" / "fw_twin.exe").is_file():
        problems.append("fw_twin/fw_twin.exe missing")
    for bad in ("PySide6", "numpy", "bend_stand", "pyqtgraph", "pytest", "PyInstaller"):
        if (internal / bad).exists():
            problems.append(f"{bad} bundled")
    size = sum(p.stat().st_size for p in dist.rglob("*") if p.is_file())
    return Result("G7 layout", not problems, None, 0.0, "; ".join(problems) or "ok",
                  {"size_mib": round(size / 2**20, 1), "files": sum(1 for p in dist.rglob("*") if p.is_file()),
                   "pictures": sorted(have)})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smoke_gate_runner.py")
    ap.add_argument("--dist", required=True, type=Path)
    ap.add_argument("--work", type=Path, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--full-twin", action="store_true", help="G3 without --quick")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    dist = a.dist.resolve()
    if a.work:
        a.work.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="gr-smoke-", dir=a.work))
    sb = Sandbox(dist, root)
    results = [g7_layout(dist), g1_version(sb), g2_list(sb)]
    r3, zp = g3_twin(sb, quick=not a.full_twin)
    results += [r3, g4_verify(sb, zp), g5_refusals(sb)]
    stray = sb.stray()
    results.append(Result("G6 stray-run rule", not stray, None, 0.0,
                          "nothing outside --out" if not stray else "written outside --out: " + ", ".join(stray[:20])))
    for r in results:
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.name}: rc={r.rc} {r.seconds:.1f} s  {r.extra}")
        for line in r.detail.splitlines():
            print(f"        {line}")
    n_fail = sum(not r.ok for r in results)
    print(f"gate-runner smoke: {len(results) - n_fail}/{len(results)} PASS")
    if a.json:
        Path(a.json).write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8")
    if not a.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())

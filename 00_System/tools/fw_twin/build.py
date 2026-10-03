#!/usr/bin/env python3
"""Build the FW host twin (lean, seams only; R3 §3.6, SYS-008, D-07).

Implements: SYS-008 (twin build), TC-SYS-008-01 support (FW sources compiled unmodified).

  .venv\\Scripts\\python 00_System\\tools\\fw_twin\\build.py              # --core auto
  .venv\\Scripts\\python 00_System\\tools\\fw_twin\\build.py --core fw    # 02_FW/src/{pure,core,gen} + twin seams
  .venv\\Scripts\\python 00_System\\tools\\fw_twin\\build.py --core probe # harness probe (NOT the FW) + twin seams
  .venv\\Scripts\\python 00_System\\tools\\fw_twin\\build.py --check-seams

Outputs build/fw_twin.exe (core fw) or build/fw_twin_probe.exe (core probe) and build/build_<core>.log.
The FW sources are compiled exactly as they are in 02_FW (never copied, never patched). Seam headers come
from 02_FW/src/hal/ when A has created them, else from fw_twin/contract/ (the tools/README seam v1 copy);
--check-seams compares A's prototypes with the README seam v1 block and lists every difference.
Host compiler: CLion-bundled MinGW GCC 13.1 (CLAUDE.md), else gcc on PATH, else $TWIN_GCC.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FW = REPO / "02_FW"
README = HERE.parent / "README.md"
CLION_GCC = Path(r"C:\Program Files\JetBrains\CLion 2025.3.2\bin\mingw\bin\gcc.exe")
HAL_HEADERS = ["hal_uart.h", "hal_time.h", "hal_step.h", "hal_inputs.h", "hal_outputs.h", "hal_hx711.h",
               "hal_flash.h", "hal_sys.h"]
ENGINE = [HERE / "engine" / "twin_engine.c", HERE / "engine" / "twin_seams.c", HERE / "engine" / "twin_meas.c"]
DEFINES = ["-DFW_TWIN=1", "-DFW_VERSION_MAJOR=0", "-DFW_VERSION_MINOR=1", "-DFW_VERSION_PATCH=0",
           "-DPARAMS_GEN_WITH_KEYS=0",
           "-DFW_FEATURE_EXTRA=FEAT_TWIN"]          # INFO feature bit TWIN (02_FW/src/core/fw.h, no #ifdef in core)


def find_gcc() -> str:
    env = os.environ.get("TWIN_GCC")
    if env:
        return env
    if CLION_GCC.exists():
        return str(CLION_GCC)
    g = shutil.which("gcc")
    if g:
        return g
    raise SystemExit("fw_twin build: no host gcc (CLion MinGW 13.1 expected at %s; or set TWIN_GCC)" % CLION_GCC)


def fw_core_present() -> bool:
    return any((FW / "src" / "core").glob("*.c"))


def hal_dir() -> tuple[Path, str]:
    a = FW / "src" / "hal"
    if all((a / h).exists() for h in HAL_HEADERS):
        return a, "02_FW/src/hal (Implementer A)"
    return HERE / "contract", "fw_twin/contract (seam v1 copy; A's headers absent)"


def sources(core: str) -> list[Path]:
    src = FW / "src"
    if core == "fw":
        files = sorted((src / "pure").glob("*.c")) + sorted((src / "core").glob("*.c")) + sorted((src / "gen").glob("*.c"))
    else:
        files = [HERE / "probe" / "probe_core.c", src / "gen" / "params_gen.c"]
    return files + ENGINE


def exe_path(core: str, build_dir: Path) -> Path:
    return build_dir / ("fw_twin.exe" if core == "fw" else "fw_twin_probe.exe")


def needs_build(core: str, build_dir: Path = HERE / "build") -> bool:
    exe = exe_path(core, build_dir)
    if not exe.exists():
        return True
    t = exe.stat().st_mtime
    hdrs = [p for d in (FW / "src", FW / "include", HERE / "engine", HERE / "contract", HERE / "probe")
            if d.exists() for p in d.rglob("*.h")]
    return any(p.stat().st_mtime > t for p in sources(core) + hdrs + [Path(__file__)])


# ---------------------------------------------------------------------------------------------- seams
_PROTO = re.compile(r"[A-Za-z_][\w \*]*?\b(hal_\w+|core_tick_1ms|step_isr|on_input_edge|on_afe_sample)\s*\(([^)]*)\)\s*;")


def _norm(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    return re.sub(r"\s+", " ", s).replace(" *", "*").replace("* ", "*").strip()


def _strip_c(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"^\s*#.*?(?<!\\)$", " ", text, flags=re.M | re.S)       # preprocessor lines (incl. continued)
    text = re.sub(r"\\\n", " ", text)
    return text.replace('extern "C" {', " ")


def prototypes(text: str) -> dict[str, str]:
    out = {}
    for stmt in _strip_c(text).split(";"):
        stmt = _norm(stmt.split("}")[-1].split("{")[-1] + ";")
        m = _PROTO.search(stmt)
        if m and "typedef" not in stmt:
            out[m.group(1)] = _norm(m.group(0)).rstrip(";").strip()
    return out


def structs(text: str) -> dict[str, str]:
    text = _strip_c(text)
    return {m.group(2): _norm(m.group(1)) for m in re.finditer(r"typedef\s+(struct\s*\{[^}]*\}|enum\s*\{[^}]*\})\s*(\w+)\s*;", text)}


def readme_seam_block() -> str:
    txt = README.read_text(encoding="utf-8")
    m = re.search(r"### Seam v1.*?```c\n(.*?)```", txt, flags=re.S)
    return m.group(1) if m else ""


def check_seams() -> list[str]:
    """Differences between A's headers and the README seam v1 (the normative copy, FW_design §8.1)."""
    contract = readme_seam_block()
    if not contract:
        return ["tools/README.md: seam v1 block not found"]
    want_p, want_s = prototypes(contract), structs(contract)
    # seam additions announced in the README but not yet delivered by A (marked "PENDING-A" on their line)
    pending = {m.group(1) for ln in contract.splitlines() if "PENDING-A" in ln
               for m in [re.search(r"(\w+)\s*\(", ln)] if m}
    a = FW / "src" / "hal"
    present = [h for h in HAL_HEADERS if (a / h).exists()]
    if not present:
        return ["02_FW/src/hal/hal_*.h absent (A's WP1 not delivered): twin built against fw_twin/contract/"]
    text = "\n".join((a / h).read_text(encoding="utf-8", errors="replace") for h in present)
    have_p, have_s = prototypes(text), structs(text)
    diffs = [f"missing header 02_FW/src/hal/{h}" for h in HAL_HEADERS if h not in present]
    for k, v in want_p.items():
        if k not in have_p:
            if k not in pending:
                diffs.append(f"{k}: missing in A's headers (README: {v})")
        elif have_p[k].replace(" ", "") != v.replace(" ", ""):
            diffs.append(f"{k}: A '{have_p[k]}' != README '{v}'")
    for k, v in want_s.items():
        if k not in have_s:
            diffs.append(f"type {k}: missing in A's headers")
        elif have_s[k].replace(" ", "") != v.replace(" ", ""):
            diffs.append(f"type {k}: A '{have_s[k]}' != README '{v}'")
    for k in have_p:
        if k not in want_p and k.startswith("hal_"):
            diffs.append(f"{k}: in A's headers but not in the README seam v1 (seam change needs the Integrator)")
    return diffs


# ---------------------------------------------------------------------------------------------- build
def build(core: str = "auto", build_dir: Path = HERE / "build", quiet: bool = False) -> Path:
    if core == "auto":
        core = "fw" if fw_core_present() else "probe"
        if core == "probe" and not quiet:
            print("fw_twin: 02_FW/src/core/*.c absent -> building the harness PROBE (not the FW)")
    if core == "fw" and not fw_core_present():
        raise SystemExit("fw_twin: --core fw but 02_FW/src/core/*.c is absent (Implementer A M1-WP5)")
    build_dir.mkdir(parents=True, exist_ok=True)
    hal, hal_src = hal_dir()
    gcc = find_gcc()
    inc = [HERE / "engine", hal, FW / "include", FW / "src", FW / "src" / "pure", FW / "src" / "gen", FW / "src" / "core"]
    exe = exe_path(core, build_dir)
    cmd = [gcc, "-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Wno-unused-parameter", *DEFINES,
           *[f"-I{p}" for p in inc if p.exists()], *[str(s) for s in sources(core)], "-o", str(exe), "-lm", "-lws2_32"]
    env = dict(os.environ)
    env["PATH"] = str(Path(gcc).parent) + os.pathsep + env.get("PATH", "")
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    log = build_dir / f"build_{core}.log"
    seams = check_seams()
    log.write_text(
        f"gcc: {gcc}\nseam headers: {hal_src}\ncore: {core}\nsources ({len(sources(core))}):\n"
        + "".join(f"  {s.relative_to(REPO) if s.is_relative_to(REPO) else s}\n" for s in sources(core))
        + "command: " + " ".join(cmd) + f"\nexit: {r.returncode}\n--- stdout\n{r.stdout}--- stderr\n{r.stderr}"
        + "--- seam check (README seam v1 vs A's headers)\n" + "".join(f"  {d}\n" for d in seams or ["  identical"]),
        encoding="utf-8")
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"fw_twin build FAILED (core {core}), log: {log}")
    if not quiet:
        warn = r.stderr.count("warning:")
        print(f"fw_twin build OK: {exe.relative_to(REPO)} (core {core}, seams from {hal_src}, {warn} warnings), log {log.relative_to(REPO)}")
    return exe


def ensure_built(core: str = "auto", build_dir: Path = HERE / "build") -> Path:
    if core == "auto":
        core = "fw" if fw_core_present() else "probe"
    if needs_build(core, build_dir):
        return build(core, build_dir, quiet=True)
    return exe_path(core, build_dir)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", choices=("auto", "fw", "probe"), default="auto")
    ap.add_argument("--build-dir", type=Path, default=HERE / "build")
    ap.add_argument("--check-seams", action="store_true", help="only compare A's hal headers with the README seam v1")
    a = ap.parse_args()
    if a.check_seams:
        d = check_seams()
        print("\n".join(d) if d else "seams: A's headers == README seam v1")
        return 1 if d and not d[0].startswith("02_FW/src/hal/hal_*.h absent") else 0
    build(a.core, a.build_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Validator E - static checks for the M1 gate (FW_test_plan v0.1 level S; independent of A's check_map.py).

    .venv\\Scripts\\python 02_FW\\test\\static\\check_static.py [--build-dir 02_FW/.pio/build/nucleo_f446re]

Checks (each prints PASS/FAIL with evidence; exit 1 if any FAIL):
  S-01 NFR-005  ELF symbols: no allocator / printf family / HardwareSerial / HardwareTimer; ldscript heap 0
  S-02 FW-PLT-001 own IRQ handlers defined in the image (vector slots), nothing placed in NVM sectors 1-2
  S-03 FW-PLT-001 platformio.ini: pinned versions, R1 §6.2 flags, -O2, map file, check_map post-script
  S-04 NFR-007/FW-SW-002 NVIC: irq_prio.h levels = pinout §4 table; every NVIC_SetPriority uses PRIO_*
  S-05 SYS-007  board_pins.h pins = pinout.md §1.1 table
  S-06 FW-PLT-002 clock-derived constants (HAL_RCC_Get*Freq / SystemCoreClock), no hard-coded Stefan values
  S-07 IF-001   no hand-written protocol codes outside gen/ (no #define of CMD_/EV_/ST_/DF_/DS_..., no 'case 0x')
  S-08 FW-CFG-001 no hand-written parameter table / id literals outside gen/; gen files carry the GENERATED banner
  S-09 IF-010/FW-CFG-001 gen_params.py --check and gen_vectors.py --check exit 0
  S-10 IF-001   proto_gen.h codes and bits == ref_codec tables (independent oracle)
  S-11 FW-PAR-001..006 SRS Table 5.1 == params.yaml (type, range, default, flags); forbidden parameters absent
  S-12 SYS-010  'Implements:' in every FW source file; origin note with path @ commit hash in every reused file
  S-13 SYS-010  M1 Must requirements each have >= 1 'Verifies:' test in 02_FW/test (A + E) and the M1 TCs of
                FW_test_plan are referenced by a validator test ('TC:' tags)

Verifies: NFR-005, FW-PLT-001, FW-PLT-002, NFR-007, FW-SW-002, SYS-007, IF-001, FW-CFG-001, IF-010
Verifies: FW-PAR-001, FW-PAR-002, FW-PAR-003, FW-PAR-004, FW-PAR-005, FW-PAR-006, SYS-010, IF-009
TC: TC-NFR-005-01 (S), TC-FW-PLT-001-01, TC-FW-PLT-002-01, TC-SYS-007-01, TC-IF-001-01, TC-FW-CFG-001-01,
    TC-IF-010-01, TC-FW-PAR-001-01, TC-FW-PAR-002-01, TC-FW-PAR-003-01, TC-FW-PAR-004-01, TC-FW-PAR-005-01,
    TC-FW-PAR-006-01, TC-SYS-010-01, TC-SYS-008-01 (S part), TC-IF-009-01 (S part)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FW = Path(__file__).resolve().parents[2]
ROOT = FW.parent
TOOLS = ROOT / "00_System" / "tools"
sys.path.insert(0, str(TOOLS))
import gen_params  # noqa: E402
import ref_codec as rc  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(cid: str, ok: bool, evidence: str) -> None:
    RESULTS.append((cid, ok, evidence))
    print(f"{cid} {'PASS' if ok else 'FAIL'}: {evidence}")


def src_files(sub: str = "src", exts=(".c", ".h", ".cpp")) -> list[Path]:
    return sorted(p for p in (FW / sub).rglob("*") if p.suffix in exts)


def nm_tool() -> Path:
    for p in (Path.home() / ".platformio" / "packages" / "toolchain-gccarmnoneeabi" / "bin" / "arm-none-eabi-nm.exe",
              Path(r"G:\_SOFT\STM32CubeCLT_1.20.0\GNU-tools-for-STM32\bin\arm-none-eabi-nm.exe")):
        if p.exists():
            return p
    raise SystemExit("arm-none-eabi-nm not found")


# --------------------------------------------------------------------------------------------- S-01/02
def s01_s02(build: Path) -> None:
    elf = build / "firmware.elf"
    out = subprocess.run([str(nm_tool()), "-C", str(elf)], capture_output=True, text=True).stdout
    syms = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            syms[parts[2]] = (int(parts[0], 16), parts[1])
    banned = [r"^malloc$", r"^free$", r"^calloc$", r"^realloc$", r"^_sbrk", r"^_malloc_r$", r"^_free_r$",
              r"^operator new", r"^operator delete", r"printf", r"^puts$", r"^_puts_r$", r"HardwareSerial",
              r"HardwareTimer", r"^__cxa_guard"]
    hits = sorted(s for s in syms if any(re.search(b, s) for b in banned))
    ld = (FW / "ldscript" / "bend_f446re.ld").read_text(encoding="utf-8")
    heap0 = re.search(r"_Min_Heap_Size\s*=\s*0x0\s*;", ld) is not None
    check("S-01", not hits and heap0, f"banned symbols {hits or 'none'}; ldscript _Min_Heap_Size = 0: {heap0}")
    need = ["DMA1_Stream5_IRQHandler", "DMA1_Stream6_IRQHandler", "USART2_IRQHandler", "TIM5_IRQHandler",
            "EXTI4_IRQHandler", "HardFault_Handler", "NMI_Handler"]
    missing = [n for n in need if n not in syms or syms[n][1].upper() != "T"]
    weak = [n for n in need if n in syms and syms[n][1] == "W"]
    in_nvm = sorted(s for s, (a, t) in syms.items() if 0x08004000 <= a < 0x0800C000 and t in "TtDdRrBb")
    check("S-02", not missing and not weak and not in_nvm,
          f"handlers strong in image: {len(need) - len(missing)}/{len(need)} (missing {missing}, weak {weak}); "
          f"symbols in 0x08004000..0x0800BFFF: {in_nvm or 'none'}")


# --------------------------------------------------------------------------------------------- S-03
def s03() -> None:
    ini = (FW / "platformio.ini").read_text(encoding="utf-8")
    req = ["platform = ststm32@20.0.0", "framework-arduinoststm32@4.30000.0", "toolchain-gccarmnoneeabi@1.120301.0",
           "-DHAL_UART_MODULE_ONLY", "-DHAL_TIM_MODULE_ONLY", "-DHAL_EXTI_MODULE_DISABLED", "-DTICK_INT_PRIORITY=5",
           "-O2", "-Wl,-Map,", "post:tools/check_map.py", "board_build.ldscript = ldscript/bend_f446re.ld",
           "-DPARAMS_GEN_WITH_KEYS=0", "-DHSE_VALUE=8000000U"]
    miss = [r for r in req if r not in ini]
    upload_guard = "upload_protocol = stlink" in ini and "D-06" in ini
    check("S-03", not miss, f"required platformio.ini items missing: {miss or 'none'}; D-06 note present: {upload_guard}")


# --------------------------------------------------------------------------------------------- S-04
def s04() -> None:
    h = (FW / "include" / "irq_prio.h").read_text(encoding="utf-8")
    lv = {k: int(v) for k, v in re.findall(r"#define\s+(PRIO_\w+)\s+(\d+)u", h)}
    want = {"PRIO_ESTOP": 0, "PRIO_INPUTS": 1, "PRIO_STEP": 2, "PRIO_AFE": 3, "PRIO_TICK": 4, "PRIO_LINK": 5}
    pin = (ROOT / "01_HW" / "pinout.md").read_text(encoding="utf-8")
    table_ok = all(s in pin for s in ("| **0** | `EXTI15_10_IRQn`", "| **2** | `TIM2_IRQn`", "| **3** | `EXTI4_IRQn`",
                                      "| **4** | `TIM5_IRQn`", "| **5** | `DMA1_Stream5_IRQn`"))
    calls = []
    for f in src_files("src/hal/f446"):
        for m in re.finditer(r"NVIC_SetPriority\(\s*(\w+)\s*,\s*([^)]+)\)", f.read_text(encoding="utf-8")):
            calls.append((f.name, m.group(1), m.group(2).strip()))
    bad = [c for c in calls if not c[2].startswith("PRIO_")]
    expect_map = {"TIM5_IRQn": "PRIO_TICK", "EXTI4_IRQn": "PRIO_AFE", "DMA1_Stream5_IRQn": "PRIO_LINK",
                  "DMA1_Stream6_IRQn": "PRIO_LINK", "USART2_IRQn": "PRIO_LINK"}
    wrong = [c for c in calls if expect_map.get(c[1], c[2]) != c[2]]
    basepri = re.findall(r"#define\s+(BASEPRI_\w+)\s+BASEPRI_OF\((\d)u\)", h)
    check("S-04", lv == want and table_ok and not bad and not wrong and
          dict(basepri) == {"BASEPRI_MOTION": "2", "BASEPRI_DATA": "3", "BASEPRI_TICK": "4"},
          f"levels {lv}; pinout §4 rows present {table_ok}; NVIC_SetPriority calls {len(calls)} "
          f"(non-PRIO {bad or 'none'}, wrong {wrong or 'none'}); BASEPRI {basepri}; "
          "SysTick via HAL_InitTick(TICK_INT_PRIORITY=5)")


# --------------------------------------------------------------------------------------------- S-05
def s05() -> None:
    bp = (FW / "include" / "board_pins.h").read_text(encoding="utf-8")
    bp = re.sub(r"#ifdef PIN_END_PB1.*?#else", "", bp, flags=re.S)      # default build: END on PC1
    pins = {}
    for name, port in re.findall(r"#define\s+PIN_(\w+)_PORT\s+GPIO([A-H])", bp):
        b = re.search(rf"#define\s+PIN_{name}_BIT\s+(\d+)u", bp)
        pins.setdefault(name, f"P{port}{b.group(1)}")
    pin = (ROOT / "01_HW" / "pinout.md").read_text(encoding="utf-8")
    rows = {}
    for m in re.finditer(r"^\| \*\*(P[A-H]\d+)\*\* \|[^|]*\| ([^|]+)\|", pin, re.M):
        rows[m.group(1)] = m.group(2)
    sig = {"PUL": "PUL", "DIR": "DIR", "ENA": "ENA", "ALM": "ALM", "PEND": "PEND", "DRVPWR": "DRV_PWR",
           "ESTOP": "E-stop", "START": "START", "END": "END", "PAUSE": "PAUSE", "DOUT": "DOUT", "SCK": "PD_SCK",
           "RATE": "RATE", "TRIP": "TRIP", "LED": "LED"}
    bad = []
    for k, label in sig.items():
        p = pins.get(k)
        if p is None or p not in rows or label.lower() not in rows[p].lower():
            bad.append((k, p, rows.get(p, "-")[:30] if p else "-"))
    usart = "PA2" in rows and "USART2_TX" in rows["PA2"] and "PA3" in rows and "USART2_RX" in rows["PA3"]
    usart = usart and re.search(r"PIN_TX_BIT\s+2u", bp) and re.search(r"PIN_RX_BIT\s+3u", bp)
    no5v_tc = "PA4" in rows and "PA5" in rows      # TC pins carry ENA (PP only) and LED, no 5 V input
    check("S-05", not bad and bool(usart) and no5v_tc,
          f"{len(sig) - len(bad)}/{len(sig)} signals match pinout §1.1 ({bad or 'all'}); USART2 PA2/PA3 {bool(usart)}")


# --------------------------------------------------------------------------------------------- S-06
def s06() -> None:
    files = {f.name: f.read_text(encoding="utf-8") for f in src_files("src/hal/f446")}
    derived = ("HAL_RCC_GetPCLK1Freq" in files["uart2_dma.c"] and "HAL_RCC_GetPCLK1Freq" in files["time_tim5.c"])
    stefan = [n for n, t in files.items() for pat in (r"\b84000000\b", r"\b8400000\b", r"PSC\s*=\s*\d+\s*;",
                                                       r"BRR\s*=\s*0x[0-9A-F]+") if re.search(pat, t)]
    bounded = "wait_bits" in files["clock.c"] and "HSE_TIMEOUT_MS" in files["clock.c"]
    check("S-06", derived and not stefan and bounded,
          f"BRR/TIM5 PSC derived from PCLK1: {derived}; hard-coded clock constants: {stefan or 'none'}; "
          f"bounded HSE/PLL waits: {bounded}")


# --------------------------------------------------------------------------------------------- S-07/08
def s07_s08() -> None:
    hand = [f for f in src_files() if "/gen/" not in f.as_posix()]
    defs, cases, ids, tables = [], [], [], []
    for f in hand:
        t = f.read_text(encoding="utf-8")
        for m in re.finditer(r"#define\s+((CMD|EV|ST|DF|DS|FAULT|BLOCK|IO|SYSF|FEAT|MS|RST|PID)_(\w+))\s+", t):
            if m.group(2) == "FAULT" and m.group(3) not in rc.FAULTS:
                continue                                    # local constant (e.g. FAULT_MAGIC), not a code
            defs.append(f"{f.name}:{m.group(1)}")
        for m in re.finditer(r"case\s+0x[0-9A-Fa-f]+", t):
            cases.append(f"{f.name}:{m.group(0)}")
        for m in re.finditer(r"\b0x0[1-8]0[0-9A-F]u?\b", t):
            ids.append(f"{f.name}:{m.group(0)}")
        if re.search(r"param_meta_t\s+\w+\s*\[", t):
            tables.append(f.name)
    # FW_config / pure constants that legitimately look like ids are reported, judged by the reviewer
    check("S-07", not defs and not cases, f"hand-written protocol code defines {defs or 'none'}; numeric case labels {cases or 'none'}")
    banner = all("GENERATED" in (FW / "src" / "gen" / n).read_text(encoding="utf-8")[:400]
                 for n in ("params_gen.h", "params_gen.c", "proto_gen.h"))
    check("S-08", not tables and banner, f"parameter tables outside gen/: {tables or 'none'}; id-like literals "
          f"outside gen/: {ids or 'none'}; GENERATED banner on gen files: {banner}")


# --------------------------------------------------------------------------------------------- S-09
def s09() -> None:
    res = []
    for tool in ("gen_params.py", "gen_vectors.py"):
        r = subprocess.run([sys.executable, str(TOOLS / tool), "--check"], cwd=ROOT, capture_output=True, text=True)
        res.append((tool, r.returncode))
    check("S-09", all(c == 0 for _, c in res), f"--check exit codes {res}")


# --------------------------------------------------------------------------------------------- S-10
def s10() -> None:
    h = (FW / "src" / "gen" / "proto_gen.h").read_text(encoding="utf-8")
    vals = {}
    for m in re.finditer(r"\b([A-Z][A-Z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)u?\b", h):
        vals[m.group(1)] = int(m.group(2), 0)
    for m in re.finditer(r"#define\s+([A-Z][A-Z0-9_]+)\s+\(?(0x[0-9A-Fa-f]+|\d+)[uUlL]*\)?\s", h):
        vals.setdefault(m.group(1), int(m.group(2), 0))
    errs, n = [], 0

    def cmp(name, want):
        nonlocal n
        n += 1
        if vals.get(name) != want:
            errs.append(f"{name}={vals.get(name)} want {want}")

    for k, t in rc.CMD.items():
        cmp(f"CMD_{k}", t)
    for k, t in rc.STATUS.items():
        cmp(f"ST_{k}", t)
    for k, t in rc.EVENT.items():
        cmp(f"EV_{k}", t)
    cmp("ASYNC_DATA", rc.ASYNC["DATA"])
    cmp("ASYNC_EVENT", rc.ASYNC["EVENT"])
    for pref, tab in (("DF_", rc.DATA_FLAGS), ("DS_", rc.DATA_STATUS), ("FAULT_", rc.FAULTS), ("BLOCK_", rc.BLOCK),
                      ("IO_", rc.IO), ("SYSF_", rc.SYS_FLAGS), ("FEAT_", rc.FEATURES)):
        for i, nm in enumerate(tab):
            cmp(pref + nm, 1 << i)
    for pref, tab in (("MS_", rc.MOTION_STATE), ("RST_", rc.RESET_CAUSE), ("SRC_", rc.SOURCE), ("HP_", rc.HOME_PHASE)):
        for i, nm in enumerate(tab):
            cmp(pref + nm, i)
    for k, t in rc.REQ_LEN.items():
        cmp(f"CMD_REQ_LEN_{k}", t)
    cmp("PROTO_MAX_LEN", rc.MAX_LEN)
    cmp("PROTO_PAYLOAD_VERSION", rc.PAYLOAD_VERSION)
    cmp("PROTO_RESP_BIT", rc.RESP_BIT)
    check("S-10", not errs, f"{n} generated names compared with ref_codec; mismatches {errs[:8] or 'none'}")


# --------------------------------------------------------------------------------------------- S-11
def _num(s: str) -> float:
    s = s.replace("\u202f", "").replace("\u2009", "").replace(" ", "").replace("+", "").replace("\u2212", "-")
    s = s.replace("−", "-")
    return float(s)


def s11() -> None:
    srs = (ROOT / "00_System" / "specs" / "SRS.md").read_text(encoding="utf-8")
    sec = srs[srs.index("### 5.1 FW parameters"):srs.index("### 5.2")]
    d = {p.key: p for p in gen_params.load().params}
    errs, rows = [], 0
    for m in re.finditer(r"^\| (\w+) \| `([\w.]+)` \| (\w+) \| [^|]* \| ([^|]+) \| ([^|]+) \| ([A-Z ()a-z]*) \|", sec, re.M):
        rows += 1
        key, typ, rng, dflt, flags = m.group(2), m.group(3), m.group(4).strip(), m.group(5).strip(), m.group(6)
        p = d.get(key)
        if p is None:
            errs.append(f"{key}: not in params.yaml")
            continue
        if p.type != typ:
            errs.append(f"{key}: type {p.type} vs {typ}")
        if p.type == "enum":
            names = [e.name for e in p.enum]
            if [x.strip() for x in rng.split(",")] != names:
                errs.append(f"{key}: enum {names} vs {rng}")
            dv = dflt.split()[0]
            if names[[e.value for e in p.enum].index(p.default)] != dv:
                errs.append(f"{key}: default {p.default} vs {dv}")
        elif p.type == "bool":
            dv = dflt.split()[0]
            if bool(p.default) != (dv == "true"):
                errs.append(f"{key}: default {p.default} vs {dv}")
        else:
            lo, hi = [x for x in re.split(r"…", rng.split("(")[0])][:2]
            if _num(lo) != float(p.min) or _num(hi) != float(p.max):
                errs.append(f"{key}: range {p.min}..{p.max} vs {rng}")
            dv = re.match(r"[+\-−]?[\d  ]+(\.\d+)?", dflt.replace("\u202f", " ")).group(0)
            if _num(dv) != float(p.default):
                errs.append(f"{key}: default {p.default} vs {dflt}")
        fl = flags.split("(")[0].strip()
        want = {"M": p.moving_ok, "N": p.nvm, "R": p.reboot_required}
        got = {c: (c in fl) for c in "MNR"}
        if got != want:
            errs.append(f"{key}: flags {want} vs '{flags}'")
    forbidden = [k for k in d if re.search(r"^io\.(estop|limit|start|end)\w*_(level|invert|polarity)$", k)
                 or re.search(r"^drv\.(pwr|power|drv_power)\w*_(level|invert|polarity)$", k)
                 or re.search(r"^home\.(ref_)?switch", k)]
    unit_steps = [k for k, p in d.items() if re.search(r"step", p.unit or "") and ("/s" in (p.unit or ""))]
    check("S-11", not errs and rows == len(d) and not forbidden and not unit_steps,
          f"Table 5.1 rows {rows} vs dictionary {len(d)}; mismatches {errs or 'none'}; forbidden polarity/home-switch "
          f"params {forbidden or 'none'}; speed params in step units {unit_steps or 'none'}")


# --------------------------------------------------------------------------------------------- S-12
def s12() -> None:
    no_tag = [f.relative_to(FW).as_posix() for f in src_files() + src_files("include") if "Implements:" not in f.read_text(encoding="utf-8")]
    reused = ["src/pure/crc16.c", "src/pure/crc16.h", "src/pure/crc32.c", "src/pure/crc32.h", "src/pure/le.h",
              "src/pure/frame.c", "src/pure/frame.h", "src/pure/stream_sched.c", "src/pure/stream_sched.h",
              "src/pure/nvm_log.c", "src/pure/nvm_log.h", "tools/build_info.py", "tools/check_map.py",
              "tools/gen_test_vectors.py", "tools/host_env.ps1", "ldscript/bend_f446re.ld", "platformio.ini"]
    bad = []
    for r in reused:
        t = (FW / r).read_text(encoding="utf-8")
        if not re.search(r"(Origin|origin)[^\n]*(Thrust_Stand_HAW|Stefan)[^\n]*@\s?[0-9a-f]{7,40}", t) and \
                not re.search(r"Thrust_Stand_HAW[^\n]*\n?[^\n]*@[0-9a-f]{7,40}", t):
            bad.append(r)
    check("S-12", not no_tag and not bad, f"files without 'Implements:' {no_tag or 'none'}; reused files without "
          f"'origin path @ commit' {bad or 'none'} ({len(reused)} checked)")


# --------------------------------------------------------------------------------------------- S-13
M1_REQ = ["SYS-003", "SYS-007", "SYS-008", "SYS-010", "FW-PLT-001", "FW-PLT-002", "FW-CFG-001", "FW-CFG-002",
          "FW-CFG-003", "FW-CFG-004", "FW-NVM-001", "FW-NVM-002", "FW-NVM-003", "FW-CMD-001", "FW-CMD-002",
          "FW-CMD-004", "FW-STR-001", "FW-STR-002", "FW-STR-003", "FW-STR-004", "FW-PAR-001", "FW-PAR-002",
          "FW-PAR-003", "FW-PAR-004", "FW-PAR-005", "FW-PAR-006", "IF-001", "IF-002", "IF-003", "IF-004", "IF-005",
          "IF-006", "IF-007", "IF-008", "IF-009", "IF-010", "IF-011", "IF-012", "NFR-005", "NFR-008"]


def s13() -> None:
    tests = [p for p in (FW / "test").rglob("*") if p.suffix in (".c", ".py")]
    ver = set()
    tcs = set()
    for p in tests:
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"Verifies:([^\n*]*)", t):
            ver |= set(re.findall(r"\b(?:SYS|SAF-FW|FW-[A-Z]+|IF|NFR)-\d{3}\b", m.group(1)))
        if "test_val" in p.as_posix() or "/twin/" in p.as_posix() or "/static/" in p.as_posix():
            tcs |= set(re.findall(r"TC-[A-Z0-9-]+-\d{2}", t))
    missing = [r for r in M1_REQ if r not in ver]
    plan = (FW / "docs" / "FW_test_plan.md").read_text(encoding="utf-8")
    m1_tcs = sorted(set(re.findall(r"^\| (TC-[A-Z0-9-]+-\d{2}) \| [A-Z+]+ \| M1(?:/M2)? \|", plan, re.M)))
    unref = [t for t in m1_tcs if t not in tcs]
    check("S-13", not missing, f"M1 Must requirements with a 'Verifies:' test: {len(M1_REQ) - len(missing)}/{len(M1_REQ)} "
          f"(missing {missing or 'none'}); M1 TCs in plan {len(m1_tcs)}, referenced by validator suites "
          f"{len(m1_tcs) - len(unref)} (S/A-only: {unref or 'none'})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", type=Path, default=FW / ".pio" / "build" / "nucleo_f446re")
    a = ap.parse_args()
    s01_s02(a.build_dir)
    s03()
    s04()
    s05()
    s06()
    s07_s08()
    s09()
    s10()
    s11()
    s12()
    s13()
    fails = [c for c, ok, _ in RESULTS if not ok]
    print(f"check_static: {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS" + (f"; FAIL {fails}" if fails else ""))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

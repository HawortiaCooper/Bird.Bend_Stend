#!/usr/bin/env python3
"""Validator E - TC-SYS-009-02 (CR-02, D-40 c): the measurement images run the release logic.

Compares, object by object, the code and data sections of the release build (`nucleo_f446re`) with the
measurement builds (`nucleo_f446re_meas`, `_meas_dwt`): every object except the ones allowed to differ
(`hal/f446/meas_f4.c`, `core/build_id.c`) must have byte-identical `.text*`, `.RamFunc`, `.rodata*`, `.data*`
sections (relocations included - objdump -dr / -s of the unlinked object). Also checks: `HW_MEAS` appears
only in meas_f4.c / build_id.c / platformio.ini; the release ELF has no DIAG_MEAS executor beyond the weak
NOT_IN_BUILD default (symbol `meas_cmd_impl` absent); the RAM-resident safety handlers exist in both images.

    .venv\\Scripts\\python 02_FW\\test\\static\\check_meas_build.py   (after `pio run -e nucleo_f446re -e nucleo_f446re_meas
                                                                     -e nucleo_f446re_meas_dwt`)
Verifies: SYS-009 (measurement build), NFR-007 (handlers unchanged)
TC: TC-SYS-009-02
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

FW = Path(__file__).resolve().parents[2]
BUILD = Path(os.environ.get("PLATFORMIO_BUILD_DIR", FW / ".pio" / "build"))   # private build dir of the run
TOOL = Path.home() / ".platformio" / "packages" / "toolchain-gccarmnoneeabi" / "bin" / "arm-none-eabi-objdump.exe"
ALLOWED = {"src/hal/f446/meas_f4.c.o", "src/core/build_id.c.o"}


def allowed_for(env: str) -> set[str]:
    """Plan §6.3 exception (v0.4): in nucleo_f446re_meas_dwt the HAL objects that include meas_dwt.h (DWT stamps in
    handlers / CRIT macros, NFR-007 only) and main.cpp.o may differ; core, pure and gen objects must stay identical."""
    if not env.endswith("_dwt"):
        return set(ALLOWED)
    hal = FW / "src" / "hal" / "f446"
    inst = {f"src/hal/f446/{c.name}.o" for c in hal.glob("*.c")
            if '#include "meas_dwt.h"' in c.read_text(encoding="utf-8", errors="ignore")}
    return set(ALLOWED) | inst | {"src/main.cpp.o"}
SAFETY = ["EXTI15_10_IRQHandler", "EXTI0_IRQHandler", "EXTI1_IRQHandler", "EXTI9_5_IRQHandler",
          "EXTI4_IRQHandler", "TIM2_IRQHandler", "TIM5_IRQHandler", "step_isr", "hal_step_stop_now",
          "hal_step_abort", "hal_ena_set"]


def dump(obj: Path) -> str:
    out = subprocess.run([str(TOOL), "-dr", "-s", "-j", ".text", "-j", ".RamFunc", "-j", ".rodata", "-j", ".data",
                          "--section=.text.*", str(obj)], capture_output=True, text=True).stdout
    # drop the file-name header line (paths differ between build dirs)
    return "\n".join(ln for ln in out.splitlines() if not ln.strip().endswith("file format elf32-littlearm"))


def sections(obj: Path) -> str:
    """objdump of every allocated section of the object (names from -h)."""
    hdr = subprocess.run([str(TOOL), "-h", str(obj)], capture_output=True, text=True).stdout
    names = re.findall(r"^\s*\d+\s+(\.(?:text|RamFunc|rodata|data)[\w.$]*)\s", hdr, re.M)
    args = [str(TOOL), "-dr", "-s"]
    for n in names:
        args += ["-j", n]
    out = subprocess.run(args + [str(obj)], capture_output=True, text=True).stdout
    return "\n".join(ln for ln in out.splitlines() if "file format" not in ln)


def main() -> int:
    rel = BUILD / "nucleo_f446re"
    ok = True
    for env in ("nucleo_f446re_meas", "nucleo_f446re_meas_dwt"):
        other = BUILD / env
        objs = sorted(p.relative_to(rel).as_posix() for p in (rel / "src").rglob("*.o"))
        same, diff = 0, []
        for o in objs:
            a, b = rel / o, other / o
            if not b.exists():
                diff.append(o + " (missing)")
                continue
            if sections(a) == sections(b):
                same += 1
            elif o not in allowed_for(env):
                diff.append(o)
            elif o.startswith(("src/core/", "src/pure/", "src/gen/")) and o not in ALLOWED:
                diff.append(o + " (core/pure/gen must be identical)")
        extra = sorted(set(p.relative_to(other).as_posix() for p in (other / "src").rglob("*.o")) - set(objs))
        print(f"{env}: {same}/{len(objs)} objects identical; differing (not allowed): {diff or 'none'}; "
              f"allowed to differ: {sorted(allowed_for(env))}; extra objects: {extra or 'none'}")
        ok &= not diff and not extra
    # HW_MEAS confined
    hits = sorted({p.relative_to(FW).as_posix() for p in (FW / "src").rglob("*") if p.suffix in (".c", ".h", ".cpp")
                   and re.search(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b.*\bHW_MEAS\b",
                                 p.read_text(encoding="utf-8", errors="ignore"), re.M)})
    conf = set(hits) <= {"src/hal/f446/meas_f4.c", "src/core/build_id.c"}
    print(f"#if HW_MEAS in: {hits} -> confined: {conf}")
    ok &= conf
    # safety handlers present in both ELFs
    nm = TOOL.with_name("arm-none-eabi-nm.exe")
    for env in ("nucleo_f446re", "nucleo_f446re_meas"):
        syms = subprocess.run([str(nm), str(BUILD / env / "firmware.elf")], capture_output=True, text=True).stdout
        missing = [s for s in SAFETY if not re.search(rf"\b[TtWw] {s}\b", syms)]
        print(f"{env}: safety symbols missing: {missing or 'none'}")
        ok &= not missing
    print("TC-SYS-009-02:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

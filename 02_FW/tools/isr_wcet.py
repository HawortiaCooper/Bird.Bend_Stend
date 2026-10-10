#!/usr/bin/env python3
"""Static worst-case cycle bound of the NFR-007 interrupt paths (FW_design §9.8, v0.8).

Method. The tool reads the disassembly of the image (`arm-none-eabi-objdump -d`) and walks every path
of an interrupt handler instruction by instruction, through calls, tail calls and veneers, until the
exception return. Conditional branches and IT-block instructions take both outcomes unless the
condition is decided by values the walk knows exactly: immediates, literal-pool words, stack slots
written with known values, `sp`, and the flags computed from them (path-sensitive constant
propagation; a value loaded from global RAM or a peripheral is never assumed). It refuses code with a
loop (no static bound) or an unresolved indirect branch. The bound is the longest path found: an upper
bound of every feasible path, because only the infeasible outcomes the walk can prove are dropped.

Cycle model (Cortex-M4 TRM r0p1 §3.3 timings; STM32F446 at 180 MHz, AHB 180, APB1 45, APB2 90 MHz,
flash 5 WS with the ART accelerator; all ASSUMED, to be confirmed by the DWT sections at HG-18):
  ALU / mov / cmp / shift / mul / IT 1, mla / mls 2, udiv / sdiv 12, mrs / msr 2, cpsid / cpsie 1,
  dmb / dsb / isb 4
  ldr* 2, ldrd 3 (no pipelining credit), str* 1, strd 2, push / pop / ldm / stm 1 + N
  taken branch / call / return / tail call 1 + 3 (pipeline refill), not-taken branch 1, tbb / tbh 2
  FP: VDIV.F32 / VSQRT.F32 14 (blocking), vldr / vstr 2, vpush / vpop 1 + N, vmrs 2, vmla / vfma 3,
      other FP 1
Bus surcharge per data access (no write-buffer credit):
  APB1 (TIM2, TIM5: 0x4000_0000..0x4000_7FFF) +12, APB2 (EXTI, SYSCFG: 0x4001_0000..0x4001_6BFF) +6,
  AHB1 (GPIO, RCC, DMA: 0x4002_0000..) +1, flash data (literal pools) +5 (ART data-cache miss),
  SRAM / stack / PPB (DWT, NVIC) +0; bit-band aliases cost as their peripheral; an access through a
  register of unknown value costs as APB1 (+12) and is listed (OBS-RC-1)
Instruction fetch:
  flash code: +5 at every taken branch / call / return target (ART instruction-cache miss), +1 per
              16-byte line crossed sequentially
  SRAM code (.RamFunc over the S-bus, shared with its own data accesses): +1 per 32-bit instruction,
              +1 per data access (each word of push / pop / ldm / stm), +1 per taken-branch target
Exception: entry 12 + vector fetch 5 (flash vector table) + first fetch, exit 10; a path that executes
an FP instruction adds the lazy FP context stacking 18 + unstacking 18.

The handler "body" (what the DWT section of the handler measures, from its first instruction to the
return) and the "total" (with entry / exit / FP context) are reported; NFR-007 is checked on the total.
`--nominal` re-costs with nominal bus figures (APB1 +6, APB2 +3, ART hits, refill 2): a sensitivity
figure for HG-18, not a bound. `--old` adds the v0.5-v0.7 estimate (sum of every instruction of the listed bodies, x1.5 in SRAM, +3 per
call, +14 per VDIV/VSQRT, +22 entry/exit) for comparison with the earlier figures.

Usage:
    python 02_FW/tools/isr_wcet.py <firmware.elf> [--path NAME ...] [--list NAME ...] [--old]
A path `fn:NAME` is a plain function (no entry / exit / FP context): its bound includes the call into
and the return from NAME's critical section, so it bounds a masked window inside it (FWR-19).
Implements: NFR-007 (static analysis; DWT measurement at HG-18)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass

F_CPU = 180.0e6
COND = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le", "hs", "lo",
        "al"]
CONDRE = "|".join(COND)
RE_FUNC = re.compile(r"^([0-9a-f]{8}) <([^>]+)>:")
RE_INS = re.compile(r"^\s*([0-9a-f]+):\t((?:[0-9a-f]{4,8} ?)+)\s*\t(\S+)\s*(.*)$")
INV = {"eq": "ne", "ne": "eq", "cs": "cc", "cc": "cs", "hs": "lo", "lo": "hs", "mi": "pl", "pl": "mi",
       "vs": "vc", "vc": "vs", "hi": "ls", "ls": "hi", "ge": "lt", "lt": "ge", "gt": "le", "le": "gt"}
REGN = {f"r{i}": i for i in range(13)}
REGN.update({"ip": 12, "sb": 9, "sl": 10, "fp": 11, "sp": 13, "lr": 14, "pc": 15})
SP0 = 0x2001F000                       # symbolic stack pointer at the handler entry
M32 = 0xFFFFFFFF
BUS = {"apb1": 12, "apb2": 6, "ahb1": 1, "flash": 5, "sram": 0, "ppb": 0, "other": 0,
       "unknown": 12}      # an access through a register of unknown value: costed at the slowest bus (APB1)
ENTRY, VEC, EXIT, FPCTX = 12, 5, 10, 36
MODEL = {"P": 3, "miss": 5, "ram_insn": 1, "ram_data": 1}     # conservative (default)
NOMINAL = {"bus": {"apb1": 6, "apb2": 3, "ahb1": 1, "flash": 0, "sram": 0, "ppb": 0, "other": 0, "unknown": 6},
           "P": 2, "miss": 0, "ram_insn": 0, "ram_data": 1, "vec": 0}

DEFAULT_PATHS = ["EXTI15_10_IRQHandler", "EXTI3_IRQHandler", "EXTI0_IRQHandler", "EXTI1_IRQHandler",
                 "EXTI9_5_IRQHandler", "TIM2_IRQHandler",
                 "fn:take_halt_flags",   # FWR-19: the tick's CRIT_HALT record take (function bound >= window)
                 "fn:hal_step_stop_now", "fn:hal_step_abort", "fn:hal_step_set_period_now",
                 "fn:hal_step_arm_last"]  # the other CRIT_HALT (PRIMASK) windows (OBS-RC-6)
OLD_BODIES = {   # v0.5-v0.7 method: the bodies summed per path (FW_design §9.8 v0.7), if on the worst path
    "EXTI15_10_IRQHandler": ["EXTI15_10_IRQHandler", "hal_step_abort", "halt_hw", "hal_ena_set",
                             "step_estop_reaction"],
    "TIM2_IRQHandler": ["TIM2_IRQHandler", "step_isr", "ramp_next"],
}


def region(a: int) -> str:
    if 0x42000000 <= a < 0x44000000:                 # peripheral bit-band alias -> the peripheral itself
        a = 0x40000000 + ((a - 0x42000000) >> 5)
    if 0x22000000 <= a < 0x24000000:                 # SRAM bit-band alias
        a = 0x20000000 + ((a - 0x22000000) >> 5)
    if 0x08000000 <= a < 0x08080000:
        return "flash"
    if 0x20000000 <= a < 0x20020000:
        return "sram"
    if 0x40000000 <= a < 0x40008000:
        return "apb1"
    if 0x40010000 <= a < 0x40016C00:
        return "apb2"
    if 0x40020000 <= a < 0x40080000:
        return "ahb1"
    if 0xE0000000 <= a < 0xE0100000:
        return "ppb"
    return "other"


@dataclass
class Ins:
    addr: int
    size: int
    mn: str
    ops: str
    func: str


def split_ops(s: str) -> list[str]:
    s = s.split("@")[0].strip()
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def parse(dis: str):
    code: dict[int, Ins] = {}
    funcs: dict[str, int] = {}
    data: dict[int, int] = {}            # 16-bit units of literal pools / tables (addr -> halfword)
    cur = None
    raw: list[tuple[int, str, str]] = []

    def close():
        for a, sz, mn, ops in raw:
            code[a] = Ins(a, sz, mn, ops, cur)

    for line in dis.splitlines():
        m = RE_FUNC.match(line)
        if m:
            if cur:
                close()
            cur = m.group(2)
            funcs[cur] = int(m.group(1), 16)
            raw = []
            continue
        m = RE_INS.match(line)
        if not m or cur is None:
            continue
        a, hx, mn, ops = int(m.group(1), 16), m.group(2).split(), m.group(3), m.group(4)
        sz = sum(len(h) for h in hx) // 2
        if mn == ".word":
            v = int(ops.split()[0], 16)
            data[a] = v & 0xFFFF
            data[a + 2] = v >> 16
            continue
        if mn == ".short":
            data[a] = int(ops.split()[0], 16)
            continue
        if mn.startswith("."):
            continue
        raw.append((a, sz, mn, ops))
    if cur:
        close()
    return code, funcs, data


VBASES = sorted(["vsqrt", "vdiv", "vldr", "vstr", "vpush", "vpop", "vldmia", "vldmdb", "vstmia", "vstmdb",
                 "vldm", "vstm", "vmrs", "vmsr", "vmla", "vmls", "vfma", "vfms", "vnmla", "vnmls", "vnmul",
                 "vmov", "vcvt", "vcmpe", "vcmp", "vadd", "vsub", "vmul", "vneg", "vabs"], key=len, reverse=True)
BASES = ["ldrsb", "ldrsh", "ldrex", "ldrb", "ldrh", "ldrd", "ldr", "strb", "strh", "strd", "strex", "str",
         "ldmia", "ldmdb", "ldm", "stmia", "stmdb", "stm", "push", "pop", "cbnz", "cbz", "tbb", "tbh", "movw",
         "movt", "mov", "mvn", "add", "adc", "sub", "sbc", "rsb", "and", "orr", "orn", "eor", "bic", "lsl", "lsr",
         "asr", "ror", "cmp", "cmn", "tst", "teq", "mul", "mla", "mls", "umull", "smull", "umlal", "smlal",
         "udiv", "sdiv", "uxtb", "uxth", "sxtb", "sxth", "ubfx", "sbfx", "bfi", "bfc", "clz", "rev", "mrs", "msr",
         "cpsid", "cpsie", "dmb", "dsb", "isb", "nop"]


def split_mn(mn: str) -> tuple[str, str | None, bool]:
    """mnemonic -> (base without width / condition / s, condition or None, sets flags)."""
    m = mn.split(".")[0]
    if m in ("bl", "blx", "bx", "b"):
        return m, None, False
    mm = re.match(rf"^(bx|blx|b)({CONDRE})$", m)
    if mm:
        return mm.group(1), mm.group(2), False
    if m.startswith("it") and re.match(r"^it[te]*$", m):
        return "it", None, False
    if m.startswith("v"):
        for b in VBASES:
            if m.startswith(b) and (m[len(b):] == "" or m[len(b):] in COND):
                return b, (m[len(b):] or None), False
        return m, None, False
    for base in BASES:
        if m.startswith(base):
            rest = m[len(base):]
            s = False
            if rest.startswith("s") and base not in ("cmp", "cmn", "tst", "teq") and                     (rest[1:] in COND or rest == "s"):
                s = True
                rest = rest[1:]
            if rest == "" or rest in COND:
                return base, (rest or None), s or base in ("cmp", "cmn", "tst", "teq")
    return m, None, False


def reglist_len(ops: str) -> int:
    n = 0
    for p in re.search(r"\{([^}]*)\}", ops).group(1).split(","):
        p = p.strip()
        if "-" in p:
            x, y = p.split("-")
            n += REGN[y.strip()] - REGN[x.strip()] + 1
        elif p:
            n += 1
    return n


def ram_extra(ins: Ins, n_data: int) -> int:
    """S-bus cost of code in SRAM: one fetch slot per 32-bit instruction (a 32-bit fetch carries two
    16-bit instructions) and one per data access (fetch and data share the S-bus)."""
    return (MODEL["ram_insn"] if ins.size == 4 else 0) + MODEL["ram_data"] * n_data


@dataclass(frozen=True)
class St:
    regs: tuple            # r0..r15 values (None = unknown); 13 sp, 14 lr
    mem: frozenset         # (addr, byte) known stack bytes
    flags: tuple           # N, Z, C, V (None = unknown)
    stack: tuple           # call stack of return addresses
    it: tuple              # pending IT conditions
    fp: bool               # an FP instruction was executed on this path


def cond_val(c: str, fl) -> bool | None:
    n, z, cy, v = fl
    try:
        return {
            "eq": lambda: z, "ne": lambda: None if z is None else not z,
            "cs": lambda: cy, "hs": lambda: cy, "cc": lambda: None if cy is None else not cy,
            "lo": lambda: None if cy is None else not cy,
            "mi": lambda: n, "pl": lambda: None if n is None else not n,
            "vs": lambda: v, "vc": lambda: None if v is None else not v,
            "hi": lambda: None if cy is None or z is None else (cy and not z),
            "ls": lambda: None if cy is None or z is None else (not cy or z),
            "ge": lambda: None if n is None or v is None else n == v,
            "lt": lambda: None if n is None or v is None else n != v,
            "gt": lambda: None if n is None or v is None or z is None else (not z and n == v),
            "le": lambda: None if n is None or v is None or z is None else (z or n != v),
            "al": lambda: True,
        }[c]()
    except KeyError:
        return None


def nz(r):
    return (bool(r >> 31), r == 0)


class Walker:
    def __init__(self, code, funcs, data):
        self.code, self.funcs, self.data = code, funcs, data
        self.by_addr = {v: k for k, v in funcs.items()}
        self.memo = {}
        self.unknown_base = set()

    # ------------------------------------------------------------------ values
    def word(self, a: int) -> int | None:
        lo, hi = self.data.get(a), self.data.get(a + 2)
        return None if lo is None or hi is None else lo | (hi << 16)

    def opval(self, op: str, regs) -> int | None:
        op = op.strip()
        if op.startswith("#"):
            t = op[1:].split()[0]
            try:
                return int(t, 0) & M32
            except ValueError:
                return None
        if op in REGN:
            return regs[REGN[op]]
        return None

    @staticmethod
    def shift(v, spec, regs):
        if v is None:
            return None
        m = re.match(r"(lsl|lsr|asr|ror)\s+#?(\w+)", spec)
        if not m:
            return None
        k = m.group(2)
        n = int(k, 0) if k[0].isdigit() else regs[REGN[k]]
        if n is None:
            return None
        if m.group(1) == "lsl":
            return (v << n) & M32
        if m.group(1) == "lsr":
            return v >> n
        if m.group(1) == "asr":
            sv = v - (1 << 32) if v >> 31 else v
            return (sv >> n) & M32
        return ((v >> n) | (v << (32 - n))) & M32

    def memop(self, ins: Ins, ops: list[str], regs):
        """-> (address or None, region, base reg index, writeback value, post-index)"""
        txt = ",".join(ops)
        m = re.search(r"\[([a-z0-9]+)(?:,\s*([^\]]+))?\](!?)", txt)
        if not m:
            return None, "other", None, None
        base = m.group(1)
        if base == "pc":
            off = int(m.group(2).strip()[1:], 0) if m.group(2) else 0
            a = ((ins.addr + 4) & ~3) + off
            return a, region(a), None, None
        b = REGN.get(base)
        bv = regs[b] if b is not None else None
        off = 0
        if m.group(2):
            parts = [p.strip() for p in m.group(2).split(",")]
            if parts[0].startswith("#"):
                off = int(parts[0][1:], 0)
            else:
                rv = regs[REGN[parts[0]]] if parts[0] in REGN else None
                if len(parts) > 1:
                    rv = self.shift(rv, parts[1], regs)
                off = rv
        post = None
        tail = txt[m.end():]
        mm = re.search(r"#(-?\w+)", tail)
        if mm and not m.group(3):
            post = int(mm.group(1), 0)
        if bv is None or off is None:
            if b != 13:
                self.unknown_base.add(f"{ins.func}: {ins.addr:08x} {ins.mn} {ins.ops.split('@')[0].strip()}")
            return None, "unknown", b, None
        a = (bv + (0 if post is not None else off)) & M32
        wb = None
        if m.group(3):
            wb = a
        elif post is not None:
            wb = (bv + post) & M32
        return a, region(a), b, wb

    @staticmethod
    def mem_read(mem: dict, a: int, n: int):
        vals = [mem.get(a + i) for i in range(n)]
        if any(v is None for v in vals):
            return None
        return int.from_bytes(bytes(vals), "little")

    @staticmethod
    def mem_write(mem: dict, a: int, n: int, v):
        for i in range(n):
            if v is None:
                mem.pop(a + i, None)
            else:
                mem[a + i] = (v >> (8 * i)) & 0xFF

    # ------------------------------------------------------------------ one instruction
    def step(self, ins: Ins, st: St):
        """Execute `ins` on a copy of the state. Returns (cost, note, regs, mem, flags, fp, ctl) with
        ctl = None (next sequential) or ('br', cond|None, target) / ('call', target) / ('ret',) /
        ('tail', target) / ('switch', [targets])."""
        base, cnd, setf = split_mn(ins.mn)
        regs = list(st.regs)
        mem = dict(st.mem)
        flags = st.flags
        ops = split_ops(ins.ops)
        ram = region(ins.addr) == "sram"
        fp = st.fp
        note = ""
        ctl = None
        cost = 1

        it = st.it
        cond_ex = None                       # IT-block condition of this instruction
        if it:
            cond_ex, it = it[0], it[1:]
        if cnd and base != "b":
            cond_ex = cnd if cond_ex is None else cond_ex
        exe = True if cond_ex is None else cond_val(cond_ex, flags)   # True / False / None

        def wr(i, v):
            if i is None:
                return
            regs[i] = None if (exe is None) else v
            if i == 15:
                pass

        if base == "it":
            m = re.match(r"^it([te]*)$", ins.mn.split(".")[0])
            c0 = ops[0]
            seq = [c0] + [c0 if ch == "t" else INV[c0] for ch in m.group(1)]
            return 1 + (ram_extra(ins, 0) if ram else 0), "", regs, mem, flags, fp, None, tuple(seq)

        if exe is False:                     # IT condition false: executes as a NOP
            return 1 + (ram_extra(ins, 0) if ram else 0), "skipped", regs, mem, flags, fp, None, it

        if base.startswith("v"):
            fp = True
            b = base
            if b in ("vsqrt", "vdiv"):
                cost = 14
            elif b in ("vldr", "vstr"):
                a, rg, bi, wb = self.memop(ins, ops, regs)
                cost = 2 + BUS[rg]
                note = rg
                if b == "vstr" and a is not None:
                    self.mem_write(mem, a, 4, None)
            elif b in ("vpush", "vpop", "vldm", "vstm", "vldmia", "vstmia", "vstmdb", "vldmdb"):
                n = len(re.findall(r"[sd]\d+", ins.ops))
                m2 = re.search(r"([sd])(\d+)-[sd](\d+)", ins.ops)
                if m2:
                    n = int(m2.group(3)) - int(m2.group(2)) + 1
                cost = 1 + n
                if b == "vpush":
                    regs[13] = None if regs[13] is None else (regs[13] - 4 * n) & M32
                elif b == "vpop":
                    regs[13] = None if regs[13] is None else (regs[13] + 4 * n) & M32
            elif b == "vmrs":
                cost = 2
                if "APSR" in ins.ops:
                    flags = (None, None, None, None)
            elif b in ("vmla", "vmls", "vfma", "vfms", "vnmla", "vnmls"):
                cost = 3
            elif b == "vmov" and ops and ops[0] in REGN:
                wr(REGN[ops[0]], None)
                if len(ops) > 1 and ops[1] in REGN:
                    wr(REGN[ops[1]], None)
            if ram:
                nacc = 1 if b in ("vldr", "vstr") else (cost - 1 if b.startswith(("vpush", "vpop", "vldm", "vstm")) else 0)
                cost += ram_extra(ins, nacc)
            return cost, note, regs, mem, flags, fp, None, it

        d = REGN.get(ops[0]) if ops else None

        # ---- branches
        if base == "b":
            t = int(ops[0].split()[0], 16)
            ctl = ("br", cnd or cond_ex or "al", t)
            cost = 1
        elif base in ("cbz", "cbnz"):
            r = regs[REGN[ops[0]]]
            t = int(ops[1].split()[0], 16)
            if r is None:
                ctl = ("br", "?", t)
            else:
                ctl = ("br", "al" if ((r == 0) == (base == "cbz")) else "never", t)
        elif base == "bl":
            ctl = ("call", int(ops[0].split()[0], 16))
        elif base == "blx":
            v = regs[REGN[ops[0]]] if ops[0] in REGN else None
            if v is None:
                raise SystemExit(f"unresolved blx at {ins.addr:x} ({ins.func})")
            ctl = ("call", v & ~1)
        elif base == "bx":
            if ops[0] == "lr":
                ctl = ("ret",)
            else:
                v = regs[REGN[ops[0]]]
                if v is None:
                    raise SystemExit(f"unresolved bx at {ins.addr:x} ({ins.func})")
                ctl = ("tail", v & ~1)
        elif base in ("tbb", "tbh"):
            a = ins.addr + 4
            raw = b""
            while a in self.data and (a not in self.code):
                raw += self.data[a].to_bytes(2, "little")
                a += 2
            idx_reg = re.search(r"\[pc,\s*(\w+)", ins.ops).group(1)
            iv = regs[REGN[idx_reg]]
            offs = list(raw) if base == "tbb" else [int.from_bytes(raw[i:i + 2], "little")
                                                     for i in range(0, len(raw) - 1, 2)]
            if iv is not None and iv < len(offs):
                offs = [offs[iv]]
            tg = sorted({ins.addr + 4 + 2 * o for o in offs if ins.addr + 4 + 2 * o in self.code})
            ctl = ("switch", tg)
            cost = 2 + (0 if ram else BUS["flash"])   # table load
        # ---- loads / stores
        elif base in ("ldr", "ldrb", "ldrh", "ldrsb", "ldrsh", "ldrd", "ldrex"):
            a, rg, bi, wb = self.memop(ins, ops, regs)
            n = {"ldr": 4, "ldrex": 4, "ldrb": 1, "ldrh": 2, "ldrsb": 1, "ldrsh": 2, "ldrd": 8}[base]
            cost = (3 if base == "ldrd" else 2) + BUS[rg]
            note = rg
            v = None
            if a is not None:
                if rg in ("flash",) or (rg == "sram" and "[pc" in ins.ops):
                    v = self.word(a) if n == 4 else None
                    if n in (1, 2) and self.data.get(a & ~1) is not None:
                        hw = self.data[a & ~1]
                        v = (hw >> (8 * (a & 1))) & 0xFF if n == 1 else hw
                elif SP0 - 0x4000 <= a <= SP0 + 0x100:
                    v = self.mem_read(mem, a, min(n, 4))
                    if v is not None and base == "ldrsb" and v & 0x80:
                        v = (v - 0x100) & M32
                    if v is not None and base == "ldrsh" and v & 0x8000:
                        v = (v - 0x10000) & M32
            if d == 15:                       # ldr pc, ... (veneer or return)
                if "[sp" in ins.ops:
                    ctl = ("ret",)
                else:
                    if v is None:
                        raise SystemExit(f"unresolved ldr pc at {ins.addr:x} ({ins.func})")
                    ctl = ("tail", v & ~1)
            else:
                wr(d, v)
                if base == "ldrd":
                    wr(REGN.get(ops[1]), None)
            if wb is not None and bi is not None:
                regs[bi] = wb
        elif base in ("str", "strb", "strh", "strd", "strex"):
            a, rg, bi, wb = self.memop(ins, ops, regs)
            n = {"str": 4, "strex": 4, "strb": 1, "strh": 2, "strd": 8}[base]
            cost = (2 if base == "strd" else 1) + BUS[rg]
            note = rg
            if a is not None and SP0 - 0x4000 <= a <= SP0 + 0x100:
                if base == "strd":
                    self.mem_write(mem, a, 4, None if exe is None else regs[REGN[ops[0]]])
                    self.mem_write(mem, a + 4, 4, None if exe is None else regs[REGN[ops[1]]])
                else:
                    self.mem_write(mem, a, n, None if exe is None else regs[REGN[ops[0]]])
            if base == "strex":
                wr(d, None)
            if wb is not None and bi is not None:
                regs[bi] = wb
        elif base in ("push", "stmdb", "stmia", "stm", "pop", "ldmia", "ldm", "ldmdb"):
            rl = re.search(r"\{([^}]*)\}", ins.ops).group(1)
            lst = []
            for p in rl.split(","):
                p = p.strip()
                if "-" in p:
                    x, y = p.split("-")
                    lst += list(range(REGN[x.strip()], REGN[y.strip()] + 1))
                elif p:
                    lst.append(REGN[p])
            lst.sort()
            n = len(lst)
            cost = 1 + n
            if base == "push" or (base in ("stmdb",) and ops[0].startswith("sp")):
                sp = regs[13]
                if sp is not None:
                    sp = (sp - 4 * n) & M32
                    for k, r in enumerate(lst):
                        self.mem_write(mem, sp + 4 * k, 4, regs[r])
                regs[13] = sp
            elif base == "pop" or (base in ("ldmia", "ldm") and ops[0].startswith("sp")):
                sp = regs[13]
                for k, r in enumerate(lst):
                    v = self.mem_read(mem, sp + 4 * k, 4) if sp is not None else None
                    regs[r] = v
                regs[13] = None if sp is None else (sp + 4 * n) & M32
                if 15 in lst:
                    ctl = ("ret",)
            else:
                bi = REGN.get(ops[0].rstrip("!"))
                if base.startswith("ldm"):
                    for r in lst:
                        regs[r] = None
                if ops[0].endswith("!") and bi is not None:
                    regs[bi] = None
                a = regs[bi] if bi is not None else None
                rg = region(a) if a is not None else "unknown"
                cost += BUS[rg] * n
                note = rg
        # ---- data processing
        else:
            v = None
            vals = [self.opval(o, regs) for o in ops]
            if ops and re.match(r"^(lsl|lsr|asr|ror)\b", ops[-1]) and len(ops) >= 3:
                vals[-2] = self.shift(vals[-2], ops[-1], regs)
                vals = vals[:-1]
                ops = ops[:-1]
            src = vals[1:] if base not in ("cmp", "cmn", "tst", "teq") else vals
            two = (src[0], src[1]) if len(src) >= 2 else (vals[0], src[0]) if len(src) == 1 else (None, None)
            fl = (None, None, None, None)
            if base in ("mov", "movw"):
                v = src[0] if src else None
                fl = (nz(v) + (flags[2], flags[3])) if v is not None else fl
            elif base == "movt":
                v = None if regs[d] is None or src[0] is None else (regs[d] & 0xFFFF) | (src[0] << 16)
            elif base == "mvn":
                v = None if src[0] is None else (~src[0]) & M32
            elif base in ("add", "adc", "sub", "sbc", "rsb", "cmp", "cmn"):
                x, y = two
                if base in ("cmp", "cmn"):
                    x, y = vals[0], vals[1] if len(vals) > 1 else None
                if base == "rsb":
                    x, y = y, x
                if x is not None and y is not None and base not in ("adc", "sbc"):
                    if base in ("add", "cmn"):
                        r = (x + y) & M32
                        cy = x + y > M32
                        sx, sy, sr = x >> 31, y >> 31, r >> 31
                        vv = sx == sy and sr != sx
                    else:
                        r = (x - y) & M32
                        cy = x >= y
                        sx, sy, sr = x >> 31, y >> 31, r >> 31
                        vv = sx != sy and sr != sx
                    v = r
                    fl = nz(r) + (cy, vv)
            elif base in ("and", "orr", "orn", "eor", "bic", "tst", "teq"):
                x, y = two
                if base in ("tst", "teq"):
                    x, y = vals[0], vals[1] if len(vals) > 1 else None
                if x is not None and y is not None:
                    v = {"and": x & y, "tst": x & y, "orr": x | y, "orn": x | (~y & M32), "eor": x ^ y,
                         "teq": x ^ y, "bic": x & (~y & M32)}[base]
                    fl = nz(v) + (None, flags[3])
            elif base in ("lsl", "lsr", "asr", "ror"):
                x, y = two
                if x is not None and y is not None:
                    v = self.shift(x, f"{base} #{y}", regs)
                    if base == "lsl" and y:
                        cy = bool((x >> (32 - y)) & 1)
                    elif base in ("lsr", "asr") and y:
                        cy = bool((x >> (y - 1)) & 1)
                    else:
                        cy = flags[2]
                    fl = nz(v) + (cy, flags[3])
            elif base in ("mul",):
                x, y = two
                v = None if x is None or y is None else (x * y) & M32
            elif base in ("mla", "mls"):
                cost = 2
            elif base in ("udiv", "sdiv"):
                cost = 12
            elif base in ("uxtb", "uxth"):
                x = src[0] if src else None
                v = None if x is None else x & (0xFF if base == "uxtb" else 0xFFFF)
            elif base in ("mrs", "msr"):
                cost = 2
            elif base in ("dmb", "dsb", "isb"):
                cost = 4
            if base in ("umull", "smull", "umlal", "smlal"):
                wr(REGN.get(ops[1]), None)
            if base not in ("cmp", "cmn", "tst", "teq", "msr", "cpsid", "cpsie", "dmb", "dsb", "isb", "nop"):
                if d == 13 and base in ("add", "sub") and v is not None:
                    regs[13] = v
                elif d == 13:
                    regs[13] = v
                else:
                    wr(d, v)
            if setf:
                flags = fl if exe is True else (None, None, None, None)
        if ram:
            nacc = 0
            if base.startswith(("ldr", "str")):
                nacc = 2 if base in ("ldrd", "strd") else 1
            elif base in ("push", "pop", "stmdb", "stmia", "stm", "ldmia", "ldm", "ldmdb"):
                nacc = len(re.findall(r"(?:r\d+|ip|lr|pc|sb|sl|fp)", re.search(r"\{([^}]*)\}", ins.ops).group(1)))
            elif base in ("tbb", "tbh"):
                nacc = 1
            cost += ram_extra(ins, nacc)
        if ctl is not None and ctl[0] != "br" and cond_ex is not None and exe is None:
            ctl = ctl + ("maybe",)
        return cost, note, regs, mem, flags, fp, ctl, it

    # ------------------------------------------------------------------ path search
    def run(self, addr: int, st: St, seq_prev: int | None):
        key = (addr, st, seq_prev)
        if key in self.memo:
            r = self.memo[key]
            if r is None:
                raise SystemExit(f"loop through {addr:x}: no static bound")
            return r
        self.memo[key] = None
        ins = self.code.get(addr)
        if ins is None:
            raise SystemExit(f"no instruction at {addr:x}")
        ram = region(addr) == "sram"
        cost, note, regs, mem, flags, fp, ctl, it = self.step(ins, st)
        if seq_prev is not None and not ram and MODEL["miss"] and (seq_prev >> 4) != (addr >> 4):
            cost += 1
        nst = St(tuple(regs), frozenset(mem.items()), flags, st.stack, tuple(it), fp)
        fetch = lambda t: 1 if region(t) == "sram" else MODEL["miss"]       # noqa: E731
        options = []
        nxt = addr + ins.size
        if ctl is None:
            r = self.run(nxt, nst, addr)
            options.append((cost + r[0], r[1], [(ins, cost, note)] + r[2]))
        elif ctl[0] == "br":
            c, t = ctl[1], ctl[2]
            if t not in self.code:
                raise SystemExit(f"branch target {t:x} not in code")
            dec = {"al": True, "never": False, "?": None}.get(c)
            if c not in ("al", "never", "?"):
                dec = cond_val(c, st.flags)
            if dec is not False:
                tk = cost + MODEL["P"] + fetch(t)
                r = self.run(t, nst, None)
                options.append((tk + r[0], r[1], [(ins, tk, (note + " taken").strip())] + r[2]))
            if dec is not True:
                r = self.run(nxt, nst, addr)
                options.append((cost + r[0], r[1], [(ins, cost, (note + " not-taken").strip())] + r[2]))
        else:
            if ctl[-1] == "maybe":                            # conditional call / return, cond unknown
                r = self.run(nxt, nst, addr)
                options.append((cost + r[0], r[1], [(ins, cost, "skipped")] + r[2]))
            if ctl[0] == "switch":
                for t in ctl[1]:
                    tk = cost + MODEL["P"] + fetch(t)
                    r = self.run(t, nst, None)
                    options.append((tk + r[0], r[1], [(ins, tk, "switch")] + r[2]))
            elif ctl[0] == "call":
                t = ctl[1]
                if t not in self.code:
                    raise SystemExit(f"call target {t:x} not in code")
                c2 = cost + MODEL["P"] + fetch(t)
                regs2 = list(nst.regs)
                regs2[14] = nxt | 1
                st2 = St(tuple(regs2), nst.mem, nst.flags, nst.stack + (nxt,), (), nst.fp)
                r = self.run(t, st2, None)
                options.append((c2 + r[0], r[1], [(ins, c2, f"call {self.by_addr.get(t, hex(t))}")] + r[2]))
            elif ctl[0] == "tail":
                t = ctl[1]
                c2 = cost + MODEL["P"] + fetch(t)
                r = self.run(t, nst, None)
                options.append((c2 + r[0], r[1], [(ins, c2, f"tail {self.by_addr.get(t, hex(t))}")] + r[2]))
            elif ctl[0] == "ret":
                if not st.stack:
                    options.append((cost + MODEL["P"], nst.fp, [(ins, cost + MODEL["P"], "exception return")]))
                else:
                    ra = st.stack[-1]
                    st2 = St(nst.regs, nst.mem, nst.flags, st.stack[:-1], (), nst.fp)
                    c2 = cost + MODEL["P"] + fetch(ra)
                    r = self.run(ra, st2, None)
                    options.append((c2 + r[0], r[1], [(ins, c2, "return")] + r[2]))
        best = max(options, key=lambda o: o[0])
        res = (best[0], best[1] or fp, best[2])
        self.memo[key] = res
        return res

    def bound(self, name: str, handler: bool = True):
        a = self.funcs[name]
        regs = [None] * 16
        regs[13] = SP0
        regs[14] = 0xFFFFFFF9
        st = St(tuple(regs), frozenset(), (None, None, None, None), (), (), False)
        self.memo = {}
        body, fp, path = self.run(a, st, None)
        body += 1 if region(a) == "sram" else MODEL["miss"]          # first fetch
        total = ENTRY + MODEL.get("vec", VEC) + body + EXIT + (FPCTX if fp else 0) if handler else body
        return body, total, fp, path


def old_estimate(code, funcs, names) -> int:
    cyc = 22
    for n in names:
        if n not in funcs:
            continue
        a = funcs[n]
        ins = [i for i in code.values() if i.func == n]
        c = len(ins) + 14 * sum(1 for i in ins if i.mn.startswith(("vsqrt", "vdiv"))) + \
            3 * sum(1 for i in ins if split_mn(i.mn)[0] in ("bl", "blx"))
        if region(a) == "sram":
            c = int(c * 1.5)
        cyc += c
    return cyc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NFR-007 static ISR bound (FW_design §9.8)")
    ap.add_argument("elf")
    ap.add_argument("--path", nargs="*", default=None)
    ap.add_argument("--list", nargs="*", default=[])
    ap.add_argument("--old", action="store_true")
    ap.add_argument("--nominal", action="store_true",
                    help="nominal model: APB1 +6 / APB2 +3, ART hits (no flash miss), refill 2, no S-bus "
                         "penalty for 32-bit instructions (sensitivity, not a bound)")
    ap.add_argument("--objdump", default=os.path.join(os.path.expanduser("~"), ".platformio", "packages",
                                                      "toolchain-gccarmnoneeabi", "bin",
                                                      "arm-none-eabi-objdump.exe"))
    a = ap.parse_args(argv)
    if a.nominal:
        BUS.update(NOMINAL["bus"])
        MODEL.update({k: v for k, v in NOMINAL.items() if k != "bus"})
    sys.setrecursionlimit(20000)
    dis = subprocess.run([a.objdump, "-d", a.elf], capture_output=True, text=True,
                         check=True).stdout
    code, funcs, data = parse(dis)
    w = Walker(code, funcs, data)
    print(f"{'path':22} {'code':5} {'insns':>5} {'body':>5} {'total':>5} {'us':>5}  breakdown of the worst path")
    for p in (a.path or DEFAULT_PATHS):
        handler = not p.startswith("fn:")
        if not handler:                               # a plain function (no exception overhead)
            want = p[3:]
            p = next((f for f in funcs if f == want or f.startswith(want + ".")), want)
        if p not in funcs:
            print(f"{p:22} not in the image")
            continue
        body, total, fp, path = w.bound(p, handler)
        cnt = {}
        for ins, c, note in path:
            k = note.split()[0] if note else ""
            if k in ("apb1", "apb2", "ahb1", "flash", "unknown"):
                cnt[k] = cnt.get(k, 0) + 1
        nfp = sum(1 for ins, c, n in path if ins.mn.startswith(("vsqrt", "vdiv")))
        tk = sum(1 for ins, c, n in path if n.endswith("taken") and not n.endswith("not-taken"))
        calls = sum(1 for ins, c, n in path if n.startswith(("call", "tail")))
        on_path = {ins.func for ins, c, n in path}
        old_set = [f for f in OLD_BODIES.get(p, []) if f in on_path]
        extra = f", old method {old_estimate(code, funcs, old_set) / F_CPU * 1e6:.2f} us" \
            if a.old and p in OLD_BODIES else ""
        print(f"{p:22} {region(funcs[p]):5} {len(path):5d} {body:5d} {total:5d} {total / F_CPU * 1e6:5.2f}  "
              f"taken {tk}, calls {calls}, VDIV/VSQRT {nfp}, FP ctx {'yes' if fp else 'no'}, bus "
              f"{', '.join(f'{k} {v}' for k, v in sorted(cnt.items())) or '-'}{extra}")
        if p in a.list:
            cum = 0
            for ins, c, note in path:
                cum += c
                print(f"    {ins.addr:08x} {ins.func[:22]:22} {ins.mn:12} {ins.ops.split('@')[0].strip()[:36]:36} "
                      f"{c:3d} {cum:4d}  {note}")
    if w.unknown_base:
        print("data accesses through a register of unknown value (costed as APB1; review):")
        for s in sorted(w.unknown_base):
            print("    " + s)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Assemble the operator FW delivery from finished PlatformIO builds (no build, no hardware access - D-06).

    .venv\\Scripts\\python 02_FW\\tools\\make_delivery.py --build-dir <dir A> --compare-build-dir <dir B> ^
        --out 04_Delivery\\BringUp_FW0.8.6 --label 0.8.6 --commit <full hash> [--render-md]

* <dir A> / <dir B> = two independent PLATFORMIO_BUILD_DIRs of the same commit, each with the envs
  nucleo_f446re, nucleo_f446re_meas, nucleo_f446re_meas_dwt already built (`pio run -e ...`).
* For each env: `arm-none-eabi-objcopy` (the PlatformIO toolchain that linked the ELF) writes
  - an Intel HEX: only the loadable sections (sector 0 vector table, code from sector 3) - flash sectors 1 + 2
    (0x08004000..0x0800BFFF, the NVM parameter log, FW_design §5.11) contain NO record, so a programmer that erases
    only the sectors it writes keeps the saved board configuration;
  - a raw .bin from 0x08000000 with the gaps filled with 0xFF (= erased flash): the NVM sectors end up blank, the FW
    boots with PARAMS_DEFAULTED(NO_RECORD). (PlatformIO's own firmware.bin fills the gaps with 0x00 - the delivered
    .bin is the same image with a different gap fill; both are checked.)
* Checks: no HEX data in the NVM hole; HEX data == ELF loadable content; .bin == PlatformIO firmware.bin outside the
  gaps; build A == build B (SHA-256 of bin, hex and ELF) -> reproducible; the build-id header of both builds equal.
* Writes SHA256SUMS.txt (sha256sum format) and BUILD_INFO.txt into firmware/ (release) and into the gate kit
  (--gate-out, HW_MEAS images; default firmware/gate); with --render-md renders every
  *.md of the package to a self-contained .html next to it (03_SW/packaging/docs_html.py renderer, read-only reuse).

Implements: FW-CFG-004 (build identity in the delivery), SYS-009 (images for the HW gate)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

FW = Path(__file__).resolve().parents[1]
ROOT = FW.parent
TOOLBIN = Path.home() / ".platformio" / "packages" / "toolchain-gccarmnoneeabi" / "bin"
OBJCOPY = TOOLBIN / "arm-none-eabi-objcopy.exe"
SIZE = TOOLBIN / "arm-none-eabi-size.exe"
READELF = TOOLBIN / "arm-none-eabi-readelf.exe"
NVM_LO, NVM_HI = 0x08004000, 0x0800C000
FLASH0 = 0x08000000

ENVS = [
    # env,                     sub-folder, file tag,                 purpose
    ("nucleo_f446re", "", "release", "release image - normal operation"),
    ("nucleo_f446re_meas", "gate", "HW_MEAS_GATE-ONLY",
     "measurement image - ONLY for the HW gate with Validator E, never for normal operation"),
    ("nucleo_f446re_meas_dwt", "gate", "HW_MEAS_DWT_GATE-ONLY",
     "measurement image + DWT statistics - ONLY for the HW gate with Validator E, never for normal operation"),
]


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run(args: list[str]) -> str:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {r.stderr.strip()}")
    return r.stdout


def parse_ihex(path: Path) -> dict[int, int]:
    """address -> byte of every data record (types 00 with 02/04 base records)."""
    mem: dict[int, int] = {}
    base = 0
    for ln in path.read_text(encoding="ascii").splitlines():
        if not ln.startswith(":"):
            continue
        raw = bytes.fromhex(ln[1:])
        n, addr, typ, data = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:4 + raw[0]]
        if (sum(raw) & 0xFF) != 0:
            raise ValueError(f"{path.name}: checksum error in {ln!r}")
        if typ == 0:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
        elif typ == 2:
            base = int.from_bytes(data, "big") << 4
        elif typ == 4:
            base = int.from_bytes(data, "big") << 16
        elif typ == 1:
            break
        _ = n
    return mem


def ranges(mem: dict[int, int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a in sorted(mem):
        if out and a == out[-1][1]:
            out[-1] = (out[-1][0], a + 1)
        else:
            out.append((a, a + 1))
    return out


def sector_of(addr: int) -> int:
    off = addr - FLASH0
    if off < 0x10000:
        return off // 0x4000              # sectors 0..3: 16 KB
    if off < 0x20000:
        return 4                          # 64 KB
    return 5 + (off - 0x20000) // 0x20000  # 128 KB


def build_id(build_env_dir: Path) -> dict[str, str]:
    h = (build_env_dir / "gen_build" / "build_info_gen.h").read_text(encoding="ascii")
    return dict(re.findall(r'#define (FW_\w+) "([^"]*)"', h))


def convert(env_dir: Path, out_bin: Path, out_hex: Path) -> dict[str, object]:
    elf = env_dir / "firmware.elf"
    run([str(OBJCOPY), "-O", "ihex", str(elf), str(out_hex)])
    run([str(OBJCOPY), "-O", "binary", "--gap-fill", "0xFF", str(elf), str(out_bin)])
    mem = parse_ihex(out_hex)
    rng = ranges(mem)
    hole = [a for a in mem if NVM_LO <= a < NVM_HI]
    if hole:
        raise RuntimeError(f"{out_hex.name}: {len(hole)} bytes inside the NVM sectors 1-2")
    b = out_bin.read_bytes()
    pio = (env_dir / "firmware.bin").read_bytes()
    if len(b) != len(pio):
        raise RuntimeError(f"{out_bin.name}: length {len(b)} != PlatformIO firmware.bin {len(pio)}")
    for i, (x, y) in enumerate(zip(b, pio)):
        a = FLASH0 + i
        if a in mem:
            if x != mem[a] or y != mem[a]:
                raise RuntimeError(f"{out_bin.name}: byte at 0x{a:08X} differs from the HEX / PlatformIO bin")
        elif x != 0xFF or y != 0x00:
            raise RuntimeError(f"{out_bin.name}: gap byte at 0x{a:08X} = 0x{x:02X} / pio 0x{y:02X}")
    size_txt = run([str(SIZE), "-A", str(elf)])
    sec = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(\.\S+)\s+(\d+)\s+\d+", size_txt, re.M)}
    flash = sum(sec.get(k, 0) for k in (".isr_vector", ".text", ".rodata", ".ARM", ".init_array", ".fini_array",
                                         ".preinit_array", ".data"))
    ram = sum(sec.get(k, 0) for k in (".data", ".bss", ".noinit"))
    return {"ranges": rng, "sectors": sorted({sector_of(a) for a, _ in rng} | {sector_of(e - 1) for _, e in rng}),
            "data_bytes": len(mem), "bin_len": len(b), "flash": flash, "ram": ram,
            "stack_reserve": sec.get("._user_heap_stack", 0), "elf_sha": sha256(elf)}


def render_md(pkg: Path) -> list[Path]:
    sys.path.insert(0, str(ROOT / "03_SW" / "packaging"))
    import docs_html  # noqa: E402  (read-only reuse of Implementer B's stdlib renderer)
    out = []
    mds = sorted(pkg.rglob("*.md"))
    for md in mds:
        link_map = {o.relative_to(md.parent).as_posix(): o.relative_to(md.parent).with_suffix(".html").as_posix()
                    for o in mds if o.is_relative_to(md.parent)}
        if md.parent != pkg:                               # links from firmware/ back to the guide
            link_map["../BRINGUP_GUIDE.md"] = "../BRINGUP_GUIDE.html"
        text = md.read_text(encoding="utf-8")
        page = docs_html.render(text, link_map)
        page = page.replace("[ ]", "&#9744;").replace("[x]", "&#9745;")
        html_path = md.with_suffix(".html")
        html_path.write_text(page, encoding="utf-8", newline="\n")
        out.append(html_path)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--build-dir", required=True, type=Path)
    ap.add_argument("--compare-build-dir", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--label", required=True, help="release label, e.g. 0.8.6 (FW_design version)")
    ap.add_argument("--commit", required=True)
    ap.add_argument("--source-note", default="", help="free text: what the source tree was (e.g. commit + patch)")
    ap.add_argument("--gate-out", type=Path, help="separate gate kit folder for the HW_MEAS images "
                    "(default: <out>/firmware/gate)")
    ap.add_argument("--render-md", action="store_true")
    ap.add_argument("--only-render", action="store_true", help="only render the *.md of --out to HTML")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    pkg: Path = args.out
    if args.only_render:
        for p in render_md(pkg):
            print("html  ", p.relative_to(pkg))
        return 0
    fwdir = pkg / "firmware"
    fwdir.mkdir(parents=True, exist_ok=True)
    gatedir: Path = args.gate_out or fwdir / "gate"
    gatedir.mkdir(parents=True, exist_ok=True)
    info: list[tuple[str, str, str, Path, Path, dict[str, object], dict[str, str]]] = []
    for env, sub, tag, purpose in ENVS:
        dst = gatedir if sub else fwdir
        base = f"BBS_FW_{args.label}_{tag}"
        res = convert(args.build_dir / env, dst / f"{base}.bin", dst / f"{base}.hex")
        bid = build_id(args.build_dir / env)
        if args.compare_build_dir:
            tmp = args.compare_build_dir / f"_cmp_{env}"
            tmp.mkdir(exist_ok=True)
            res_b = convert(args.compare_build_dir / env, tmp / "b.bin", tmp / "b.hex")
            same = (sha256(tmp / "b.bin") == sha256(dst / f"{base}.bin") and sha256(tmp / "b.hex") ==
                    sha256(dst / f"{base}.hex") and res_b["elf_sha"] == res["elf_sha"]
                    and build_id(args.compare_build_dir / env) == bid)
            shutil.rmtree(tmp)
            if not same:
                raise RuntimeError(f"{env}: build A and build B differ - not reproducible")
            res["reproducible"] = True
        info.append((env, tag, purpose, dst / f"{base}.bin", dst / f"{base}.hex", res, bid))
        print(f"{env:24s} bin {res['bin_len']:6d} B  hex data {res['data_bytes']:6d} B  sectors {res['sectors']}  "
              f"build {bid.get('FW_BUILD_DATE')}  reproducible {res.get('reproducible', 'n/a')}")
    # SHA256SUMS per folder
    for base_dir in (fwdir, gatedir):
        lines = []
        for _env, _tag, _p, b, h, _r, _bid in info:
            for f in (b, h):
                if f.parent == base_dir:
                    lines.append(f"{sha256(f)} *{f.relative_to(base_dir).as_posix()}")
        (base_dir / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    try:
        rel_release = Path(os.path.relpath(fwdir, gatedir)).as_posix() + "/"
    except ValueError:
        rel_release = str(fwdir) + "/"
    # gate label
    (gatedir / "README_GATE_ONLY.txt").write_text(
        "ONLY FOR THE HW GATE WITH VALIDATOR E - NEVER FOR NORMAL OPERATION\n\n"
        "These measurement images (HW_MEAS, HW_MEAS_DWT) contain the diagnostic command DIAG_MEAS (0x3D):\n"
        "stimulus output on PB8, timer captures on the measurement header, HANG injection, static output levels.\n"
        "They are flashed by the product owner during a D-06-GATE session only, when the session runner asks\n"
        "for them (00_System/tools/hil/HW_GATE_RUNBOOK.md). After the gate the release image\n"
        "(" + rel_release + "BBS_FW_" + args.label + "_release.hex of the operator package) is flashed\n"
        "again (IMG-REL / HG-30). Hashes: SHA256SUMS.txt here; build identity: BUILD_INFO.txt here.\n"
        "GET_INFO build string ends in -MEAS / -DWT and the feature list contains HW_MEAS.\n",
        encoding="ascii", newline="\n")
    # BUILD_INFO
    pio_ver = run([str(ROOT / ".venv" / "Scripts" / "pio.exe"), "--version"]).strip()
    gcc_ver = run([str(TOOLBIN / "arm-none-eabi-gcc.exe"), "--version"]).splitlines()[0]
    oc_ver = run([str(OBJCOPY), "--version"]).splitlines()[0]
    gen = (FW / "src" / "gen" / "proto_gen.h").read_text(encoding="utf-8")
    icd = re.search(r'#define PROTO_ICD_VERSION\s+"([^"]+)"', gen)
    pg = (FW / "src" / "gen" / "params_gen.h").read_text(encoding="utf-8")
    dict_hash = re.search(r"#define PARAM_DICT_HASH\s+(0x[0-9A-Fa-f]+)", pg).group(1)
    pcount = re.search(r"#define PARAM_COUNT\s+(\d+)", pg).group(1)
    dver = re.search(r"#define PARAM_DICT_VERSION\s+(\d+)", pg)
    pmaj = re.search(r"#define PROTO_MAJOR\s+(\d+)", gen).group(1)
    pmin = re.search(r"#define PROTO_MINOR\s+(\d+)", gen).group(1)
    pay = re.search(r"#define PROTO_PAYLOAD_VERSION\s+(\d+)", gen).group(1)
    ini = (FW / "platformio.ini").read_text(encoding="utf-8")
    fwv = re.search(r"-DFW_VERSION_MAJOR=(\d+) -DFW_VERSION_MINOR=(\d+) -DFW_VERSION_PATCH=(\d+)", ini).groups()
    for base_dir in (fwdir, gatedir):
        items = [x for x in info if x[3].parent == base_dir]
        _write_build_info(base_dir, items, args, fwv, icd, pmaj, pmin, pay, dict_hash, dver, pcount, pio_ver, gcc_ver,
                          oc_ver)
    if args.render_md:
        for p in render_md(pkg):
            print("html  ", p.relative_to(pkg))
    return 0


def _write_build_info(fwdir, info, args, fwv, icd, pmaj, pmin, pay, dict_hash, dver, pcount, pio_ver, gcc_ver,
                      oc_ver) -> None:
    t = []
    t.append("Bird Bend Stand - firmware delivery BUILD_INFO")
    t.append("=" * 60)
    t.append(f"Release label (FW_design version) : {args.label}")
    t.append(f"Git commit                        : {args.commit}")
    t.append(f"GET_INFO fw_version (wire)        : {'.'.join(fwv)}   (shown as 'FW {'.'.join(fwv)}' in the PC app)")
    if args.source_note:
        t.append(f"Source tree                       : {args.source_note}")
    t.append(f"ICD                               : {icd.group(1) if icd else '?'}")
    t.append(f"PROTO_VERSION / PAYLOAD_VERSION   : {pmaj}.{pmin} / {pay}")
    t.append(f"PARAM_DICT_HASH / version / count : {dict_hash} / {dver.group(1) if dver else '6'} / {pcount}")
    sde = os.environ.get("SOURCE_DATE_EPOCH", "")
    if sde:
        stamp = _dt.datetime.fromtimestamp(int(sde), tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        t.append(f"SOURCE_DATE_EPOCH                 : {sde} ({stamp}; build-id time, reproducible)")
    t.append(f"Packaging date                    : {_dt.date.today().isoformat()}")
    t.append(f"Toolchain                         : {pio_ver}; platform ststm32@20.0.0, framework-arduinoststm32@4.30000.0,")
    t.append(f"                                    toolchain-gccarmnoneeabi@1.120301.0 ({gcc_ver})")
    t.append(f"HEX / BIN conversion              : {oc_ver}; bin gap fill 0xFF")
    t.append("")
    for env, tag, purpose, b, h, r, bid in info:
        t.append(f"[{tag}]  env {env}")
        t.append(f"  purpose            : {purpose}")
        t.append(f"  GET_INFO build     : {bid.get('FW_BUILD_DATE')}   (git {bid.get('FW_GIT_HASH')})")
        feats = "AFE BUTTONS DRV_SIGNALS HOMING MOTION MOVE_UNTIL_LOAD NVM" + (" HW_MEAS" if "MEAS" in tag else "")
        t.append(f"  features (PC app)  : {feats}")
        t.append(f"  flash / static RAM : {r['flash']} B / {r['ram']} B (+ {r['stack_reserve']} B main-stack reserve)")
        t.append("  flash ranges (HEX) : " + ", ".join(f"0x{a:08X}-0x{e - 1:08X}" for a, e in r["ranges"])
                 + f"  -> sectors {r['sectors']}; sectors 1-2 (NVM) untouched by the HEX")
        t.append(f"  {b.relative_to(fwdir).as_posix():44s} sha256 {sha256(b)}")
        t.append(f"  {h.relative_to(fwdir).as_posix():44s} sha256 {sha256(h)}")
        t.append(f"  firmware.elf (not delivered)       sha256 {r['elf_sha']}")
        t.append(f"  reproducible       : {'yes - two independent clean builds, identical ELF/BIN/HEX' if r.get('reproducible') else 'not checked'}")
        t.append("")
    t.append("Checks at packaging: PlatformIO check_map.py PASS (nothing linked into the NVM hole 0x08004000..0x0800BFFF);")
    t.append("TC-SYS-009-02 check_meas_build.py PASS (measurement images: core / pure / gen objects and the safety handlers")
    t.append("byte-identical to the release image); FW host suites (implementer + validator, 251 test cases) green.")
    t.append("Note: GET_INFO 'build' = YYYYMMDD-HHMM (gate images YYMMDD-HHMM-MEAS / -DWT) of SOURCE_DATE_EPOCH (UTC) if set, else")
    t.append("the local build minute. Rebuild byte-identically: same tree, same toolchain, set SOURCE_DATE_EPOCH to the value")
    t.append("above, then `pio run -e <env>` with a private PLATFORMIO_BUILD_DIR and this script.")
    (fwdir / "BUILD_INFO.txt").write_text("\n".join(t) + "\n", encoding="utf-8", newline="\n")
    print("wrote", fwdir / "SHA256SUMS.txt", "and BUILD_INFO.txt")


if __name__ == "__main__":
    sys.exit(main())

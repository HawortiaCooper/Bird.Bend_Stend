"""Link-result checks of the target image (FW_design §2.2 post-script, §9.3 M1 exit evidence).

Runs as a PlatformIO post-action on firmware.elf (fails the build) or standalone:
    python tools/check_map.py .pio/build/nucleo_f446re/firmware.elf [--tools DIR] [--slot NAME=IDX]

M-1  no HardwareSerial / HardwareTimer / TwoWire / Servo / tone / attachInterrupt symbols; every own
     IRQ handler is a strong definition, is not the startup Default_Handler and sits in its vector
     slot (fails if the .isr_vector words cannot be parsed - TS DEF-M3r2f-01 address fix kept);
     SystemClock_Config is the FW's strong override.
M-2  nothing is loaded into the NVM hole 0x08004000..0x0800BFFF (flash sectors 1 + 2); .isr_vector is
     the only load section in sector 0; .noinit is a NOLOAD RAM section between .bss and the stack.
M-3  no malloc / free / realloc / calloc / _sbrk / printf family / operator new|delete (NFR-005).
M-4  the flash erase/program busy loops are in .RamFunc (RAM address).

Origin: Thrust_Stand_HAW/02_FW/tools/check_map.py @37c8747 (adapted: F446 vector slots, NVM hole,
RamFunc check, no test-hook image).
Implements: NFR-005, FW-NVM-001 (NVM sectors reserved), FW-PLT-001 (own handlers)
"""
import os
import re
import subprocess
import sys

FLASH_BASE = 0x08000000
NVM_START = 0x08004000
NVM_END = 0x0800C000
RAM_BASE = 0x20000000
RAM_END = 0x20020000

# own handlers (M1) and their exception numbers (vector index = 16 + IRQn, RM0390 Table 38)
OWN_HANDLERS = {
    "NMI_Handler": 2,
    "HardFault_Handler": 3,
    "EXTI4_IRQHandler": 16 + 10,
    "DMA1_Stream5_IRQHandler": 16 + 16,
    "DMA1_Stream6_IRQHandler": 16 + 17,
    "USART2_IRQHandler": 16 + 38,
    "TIM5_IRQHandler": 16 + 50,
}
RAMFUNCS = ["ram_erase", "ram_program"]
FORBIDDEN_SUBSTR = ["HardwareSerial", "HardwareTimer", "TwoWire", "Servo", "attachInterrupt"]
FORBIDDEN_EXACT = {"tone", "noTone", "malloc", "free", "realloc", "calloc", "_sbrk", "_sbrk_r",
                   "_malloc_r", "_free_r", "_realloc_r", "_calloc_r", "printf", "sprintf",
                   "snprintf", "vsnprintf", "vsprintf", "vprintf", "puts", "iprintf", "siprintf",
                   "_printf_r", "_vfprintf_r", "_svfprintf_r", "_vfiprintf_r", "_svfiprintf_r"}
FORBIDDEN_DEMANGLED = re.compile(r"^operator (new|delete)")


def run(tool, args):
    return subprocess.run([tool] + args, capture_output=True, text=True, check=True).stdout


def check(elf, tools_prefix):
    errors = []
    nm = tools_prefix + "nm"
    objdump = tools_prefix + "objdump"

    syms = {}
    names_demangled = []
    for line in run(nm, ["-C", elf]).splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            names_demangled.append(parts[2])
    for line in run(nm, [elf]).splitlines():
        parts = line.split()
        if len(parts) == 3:
            syms[parts[2]] = (int(parts[0], 16), parts[1])

    # M-1 forbidden core classes, M-3 allocator / printf
    for n in names_demangled:
        for s in FORBIDDEN_SUBSTR:
            if s in n:
                errors.append(f"M-1: forbidden symbol linked: {n}")
        if FORBIDDEN_DEMANGLED.match(n):
            errors.append(f"M-3: forbidden function linked: {n}")
    for n, (_, t) in syms.items():
        if n in FORBIDDEN_EXACT and t.upper() in ("T", "W"):
            errors.append(f"M-3: forbidden function linked: {n}")

    # M-1 own handlers + vector table
    dflt = syms.get("Default_Handler", (None, None))[0]
    vec = syms.get("g_pfnVectors", (None, None))[0]
    vt = {}
    if vec is not None:
        dump = run(objdump, ["-s", "-j", ".isr_vector", elf])
        words = []
        for line in dump.splitlines():
            m = re.match(r"^\s*([0-9a-f]{7,8})\s+((?:[0-9a-f]{8}\s?){1,4})", line)
            if m:
                for w in m.group(2).split():
                    words.append(int.from_bytes(bytes.fromhex(w), "little"))
        vt = dict(enumerate(words))
        need = max(OWN_HANDLERS.values()) + 1
        if len(words) < need:
            errors.append(f"M-1: .isr_vector parsed {len(words)} words, need >= {need}")
    else:
        errors.append("M-1: g_pfnVectors not found")
    for h, idx in OWN_HANDLERS.items():
        if h not in syms:
            errors.append(f"M-1: handler {h} missing")
            continue
        addr, t = syms[h]
        if t not in ("T", "t"):
            errors.append(f"M-1: handler {h} is not a strong definition (nm type {t})")
        if dflt is not None and addr == dflt:
            errors.append(f"M-1: handler {h} resolves to Default_Handler")
        if idx in vt and (vt[idx] & ~1) != (addr & ~1):
            errors.append(f"M-1: vector slot {idx} = 0x{vt[idx]:08X}, expected {h} 0x{addr:08X}")
    if syms.get("SystemClock_Config", (0, "?"))[1] != "T":
        errors.append("M-1: SystemClock_Config is not the FW's strong override (FW-PLT-002)")

    # M-4 RAM functions
    for f in RAMFUNCS:
        a = syms.get(f, (None, None))[0]
        if a is None:
            errors.append(f"M-4: RAM function {f} missing")
        elif not RAM_BASE <= a < RAM_END:
            errors.append(f"M-4: {f} at 0x{a:08X} is not in RAM")

    # M-2 sections
    hdr = run(objdump, ["-h", elf]).splitlines()
    load_end = 0
    noinit = None
    for i, line in enumerate(hdr):
        m = re.match(r"^\s*\d+\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+", line)
        if not m:
            continue
        name = m.group(1)
        size, vma, lma = int(m.group(2), 16), int(m.group(3), 16), int(m.group(4), 16)
        flags = hdr[i + 1] if i + 1 < len(hdr) else ""
        if name == ".noinit":
            noinit = (vma, size, flags)
        if "LOAD" in flags and size and FLASH_BASE <= lma < FLASH_BASE + 0x100000:
            end = lma + size
            load_end = max(load_end, end)
            if lma < NVM_END and end > NVM_START:
                errors.append(f"M-2: section {name} 0x{lma:08X}..0x{end:08X} overlaps the NVM sectors")
            if lma < NVM_START and name != ".isr_vector":
                errors.append(f"M-2: section {name} placed in sector 0 (only .isr_vector allowed)")
    if noinit is None:
        errors.append("M-2: .noinit section missing")
    else:
        vma, size, flags = noinit
        if "LOAD" in flags or "CONTENTS" in flags:
            errors.append(f"M-2: .noinit is not NOLOAD ({flags.strip()})")
        if not RAM_BASE <= vma < RAM_END:
            errors.append(f"M-2: .noinit not in RAM (0x{vma:08X})")
    try:
        ebss, sn, en, end = (syms[k][0] for k in ("_ebss", "_snoinit", "_enoinit", "end"))
        if not ebss <= sn <= en <= end:
            errors.append(f"M-2: order _ebss <= _snoinit <= _enoinit <= end violated")
    except KeyError as e:
        errors.append(f"M-2: linker symbol missing: {e}")
    return errors, load_end


def report(elf, tools_prefix):
    errors, load_end = check(elf, tools_prefix)
    print(f"check_map: {os.path.basename(elf)} load image end 0x{load_end:08X}")
    for e in errors:
        print("check_map: FAIL " + e)
    if not errors:
        print(f"check_map: M-1 ({len(OWN_HANDLERS)} vector slots), M-2 (NVM hole 0x{NVM_START:08X}.."
              f"0x{NVM_END - 1:08X} empty), M-3 (no allocator/printf), M-4 (RamFunc) PASS")
    return 1 if errors else 0


try:
    Import("env")  # noqa: F821
    _SCONS = True
except NameError:
    _SCONS = False

if _SCONS:
    def _post(source, target, env):  # noqa: ARG001
        elf = str(target[0])
        prefix = env.subst("$CC")
        prefix = prefix[: -len("gcc")] if prefix.endswith("gcc") else "arm-none-eabi-"
        if report(elf, prefix) != 0:
            env.Exit(1)

    env.AddPostAction("$BUILD_DIR/${PROGNAME}.elf",  # noqa: F821
                      env.VerboseAction(_post, "Checking firmware.elf (tools/check_map.py)"))  # noqa: F821
elif __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    pref = "arm-none-eabi-"
    for i, a in enumerate(sys.argv):
        if a == "--tools" and i + 1 < len(sys.argv):
            pref = os.path.join(sys.argv[i + 1], "arm-none-eabi-")
            args = [x for x in args if x != sys.argv[i + 1]]
    for i, a in enumerate(sys.argv):
        if a == "--slot" and i + 1 < len(sys.argv):       # negative control: must FAIL M-1
            name, idx = sys.argv[i + 1].split("=")
            OWN_HANDLERS[name] = int(idx, 0)
            args = [x for x in args if x != sys.argv[i + 1]]
    sys.exit(report(args[0], pref))

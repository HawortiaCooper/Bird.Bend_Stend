#!/usr/bin/env python3
"""BirdBendStand-GateRunner — entry point of the frozen gate runner (D-57). Owner: Validator E (the Integrator
reviews; B freezes it with PyInstaller into the gate kit).

    BirdBendStand-GateRunner.exe --approval <approval file> --port COM7 --out <folder> [--from HG-28] [--only …]
    BirdBendStand-GateRunner.exe --twin --out <folder> [--quick]          rehearsal on the bundled FW twin (no board)
    BirdBendStand-GateRunner.exe --verify-zip <results zip>               check a results zip (manifest, hashes)
    BirdBendStand-GateRunner.exe --list                                   the ordered plan
    BirdBendStand-GateRunner.exe                                          (no arguments) asks for the three inputs

Everything else is hil_session.main (same options). The runner writes only into --out (standing data rule).
Source run (Validator E, tests): python 00_System/tools/hil/gate_runner.py … (same behaviour, runner hash = the
source files, gate_res.runner_identity()).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gate_export  # noqa: E402
import gate_res  # noqa: E402
import hil_session  # noqa: E402

BANNER = (f"{gate_res.RUNNER_NAME} {gate_res.RUNNER_VERSION} — Bird Bend Stand hardware gate (D-57).\n"
          "Two persons at the bench; the red E-stop and the 48 V switch within reach. Type 'abort' at any prompt to "
          "stop safely.")


def _interactive(input_fn=input) -> list[str]:
    print(BANNER)
    ap = input_fn("Approval file (drag it into this window, then Enter): ").strip().strip('"')
    port = input_fn("COM port named by the product owner (e.g. COM7): ").strip()
    default_out = Path(ap).with_name(Path(ap).stem + "_session") if ap else Path.cwd() / "gate_session"
    out = input_fn(f"Output folder [{default_out}]: ").strip().strip('"') or str(default_out)
    return ["--approval", ap, "--port", port, "--out", out]


def main(argv: list[str] | None = None, input_fn=input) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if argv[:1] == ["--verify-zip"] and len(argv) == 2:
        problems = gate_export.verify(Path(argv[1]))
        print("results zip intact" if not problems else "results zip PROBLEMS:\n  " + "\n  ".join(problems))
        return 0 if not problems else 1
    if argv[:1] == ["--version"]:
        ri = gate_res.runner_identity()
        print(f"{ri['name']} {ri['version']} ({ri['kind']}) sha256 {ri['sha256']}")
        return 0
    if not argv:
        argv = _interactive(input_fn)
    else:
        print(BANNER)
    return hil_session.main(argv)


if __name__ == "__main__":
    sys.exit(main())

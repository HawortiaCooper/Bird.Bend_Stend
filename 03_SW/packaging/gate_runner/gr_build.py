"""Build helper of BirdBendStand-GateRunner (D-57, GR-1): version stamp, FW twin staging, runner hash, BUILD_INFO,
zip. Used by ``build_gate_runner.ps1``; stdlib + ``pefile`` (pinned in the build lock).

    gr_build.py info   --out <build_info.json>
    gr_build.py stage  --info <json> --twin-build-dir <private dir> --stage <dir>     # build.py --core fw + normalise
    gr_build.py finalize --info <json> --dist <…/BirdBendStand-GateRunner>           # hash check + BUILD_INFO.txt
    gr_build.py zip    --info <json> --dist <…/BirdBendStand-GateRunner>

Reproducibility: the twin and the runner executable get their PE ``TimeDateStamp`` set to the HEAD commit time and the
PE checksum recomputed (the only bytes that differ between two builds of the same sources); everything else is
deterministic for a fixed environment (``requirements-gaterunner.lock.txt``).

Implements: D-57 (frozen runner identity = gate_res.bundle_sha256, the value make_approval.py --runner-exe computes)
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACK = HERE.parent
REPO = PACK.parents[1]
TOOLS = REPO / "00_System" / "tools"
HIL = TOOLS / "hil"
sys.path.insert(0, str(PACK))
import buildinfo  # noqa: E402

NAME = "BirdBendStand-GateRunner"
#: repository paths whose state decides the runner's ``dirty`` flag (everything that ends up in the bundle)
GR_PATHS = ("00_System/tools/hil", "00_System/tools/ref_codec.py", "00_System/tools/gen_params.py",
            "00_System/tools/gen_protocol.py", "00_System/tools/fw_twin", "00_System/specs/params.yaml",
            "01_HW/diagrams/bench", "02_FW/src", "02_FW/platformio.ini", "03_SW/packaging/gate_runner",
            "03_SW/requirements-gaterunner.txt", "03_SW/requirements-gaterunner.lock.txt")


def runner_version() -> str:
    m = re.search(r'^RUNNER_VERSION\s*=\s*"([0-9.]+)"', (HIL / "gate_res.py").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("gate_res.py: RUNNER_VERSION not found")
    return m.group(1)


def normalise_pe(path: Path, epoch: int) -> None:
    """Set the PE TimeDateStamp to ``epoch`` and recompute the PE checksum (reproducible binaries)."""
    import pefile  # noqa: PLC0415

    pe = pefile.PE(str(path))
    try:
        pe.FILE_HEADER.TimeDateStamp = int(epoch) & 0xFFFFFFFF
        pe.OPTIONAL_HEADER.CheckSum = 0
        data = bytearray(pe.write())
    finally:
        pe.close()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(bytes(data))
    pe2 = pefile.PE(str(tmp))
    try:
        pe2.OPTIONAL_HEADER.CheckSum = pe2.generate_checksum()
        data = bytearray(pe2.write())
    finally:
        pe2.close()
    tmp.unlink()
    path.write_bytes(bytes(data))


def stage(info: buildinfo.BuildInfo, twin_build_dir: Path, stage_dir: Path) -> Path:
    """Build the FW twin of this checkout (00_System/tools/fw_twin/build.py --core fw, private build dir) and stage a
    normalised copy as ``<stage>/fw_twin/fw_twin.exe``."""
    r = subprocess.run([sys.executable, str(TOOLS / "fw_twin" / "build.py"), "--core", "fw", "--build-dir",
                        str(twin_build_dir)], capture_output=True, text=True, timeout=900, check=False)
    print(r.stdout.strip())
    if r.returncode != 0:
        raise SystemExit(f"twin build failed (exit {r.returncode}):\n{r.stderr.strip()}")
    src = twin_build_dir / "fw_twin.exe"
    dst = stage_dir / "fw_twin" / "fw_twin.exe"
    if dst.parent.exists():
        shutil.rmtree(dst.parent)
    dst.parent.mkdir(parents=True)
    shutil.copyfile(src, dst)
    normalise_pe(dst, info.commit_time)
    print(f"staged {dst} ({dst.stat().st_size} B, sha256 {buildinfo_sha(dst)})")
    return dst


def buildinfo_sha(p: Path) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(p.read_bytes()).hexdigest()


def repo_bundle_sha(exe: Path) -> str:
    """The runner hash exactly as ``make_approval.py --runner-exe`` computes it (repository ``gate_res``)."""
    if str(HIL) not in sys.path:
        sys.path.insert(0, str(HIL))
    import gate_res  # noqa: PLC0415

    return gate_res.bundle_sha256(exe, gate_res.bundle_root_of(exe))


def frozen_version_line(exe: Path) -> str:
    r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=120, check=False,
                       cwd=str(exe.parent))
    return (r.stdout.strip() or r.stderr.strip()).splitlines()[-1] if (r.stdout or r.stderr) else f"(rc {r.returncode})"


def finalize(info: buildinfo.BuildInfo, dist: Path) -> int:
    exe = dist / f"{NAME}.exe"
    normalise_pe(exe, info.commit_time)
    line = frozen_version_line(exe)
    sha = repo_bundle_sha(exe)
    ok = sha in line
    internal = dist / "_internal"
    files = sorted(p.relative_to(internal).as_posix() for d in ("resources", "fw_twin")
                   for p in (internal / d).rglob("*") if p.is_file())
    size = sum(p.stat().st_size for p in dist.rglob("*") if p.is_file())
    text = [f"{NAME} — frozen hardware-gate runner (D-57)", "",
            f"runner          {line}",
            f"runner sha256   {sha}   (exe + _internal/resources + _internal/fw_twin; = make_approval.py --runner-exe)",
            f"version         {info.version}",
            f"git             {info.git_hash}{' (dirty)' if info.dirty else ''}",
            f"built (UTC)     {info.build_time}",
            f"python          {info.python} (frozen, PyInstaller one-folder, console)",
            f"twin sha256     {buildinfo_sha(internal / 'fw_twin' / 'fw_twin.exe')}",
            f"params.yaml     {buildinfo_sha(internal / 'resources' / 'params.yaml')}",
            "", "components (SHA-256; the runner hash covers exactly these):",
            f"  {buildinfo_sha(exe)}  {exe.name}",
            *[f"  {buildinfo_sha(internal / f)}  _internal/{f}" for f in files], "",
            "Usage (operator, see the gate kit README):",
            f"  {NAME}.exe                     asks for the approval file, the COM port and the output folder",
            f"  {NAME}.exe --approval <file> --port COMn --out <folder>",
            f"  {NAME}.exe --twin --out <folder> [--quick]     rehearsal on the bundled FW twin (no board)",
            f"  {NAME}.exe --verify-zip <results zip>  |  --version  |  --list", ""]
    (dist / "BUILD_INFO.txt").write_text("\n".join(text), encoding="utf-8")
    print(f"dist   {dist}  {size / 2**20:.1f} MiB  {sum(1 for p in dist.rglob('*') if p.is_file())} files")
    print(f"exe    {line}")
    print(f"hash   repo gate_res.bundle_sha256 = {sha}  ({'matches' if ok else 'DIFFERS FROM'} --version)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gr_build.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("info")
    i.add_argument("--out", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--info", required=True)
    s.add_argument("--twin-build-dir", required=True, type=Path)
    s.add_argument("--stage", required=True, type=Path)
    for n in ("finalize", "zip"):
        f = sub.add_parser(n)
        f.add_argument("--info", required=True)
        f.add_argument("--dist", required=True, type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "info":
        info = buildinfo.collect(base_version=runner_version(), packaged=GR_PATHS)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(info.to_json(), encoding="utf-8")
        print(info.to_json())
        return 0
    if not Path(a.info).is_file():                        # never fall back to a fresh (different) version
        raise SystemExit(f"--info {a.info}: no such build_info.json (run 'info --out' first)")
    info = buildinfo.load_or_collect(a.info)
    if a.cmd == "stage":
        stage(info, a.twin_build_dir, a.stage)
        return 0
    if a.cmd == "finalize":
        return finalize(info, a.dist)
    out = a.dist.parent / f"{NAME}-{info.version.replace('+', '-').replace('.dirty', '-dirty')}.zip"
    z = buildinfo.zip_dist(a.dist, info, out)
    print(f"zip    {z}  {z.stat().st_size / 2**20:.1f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

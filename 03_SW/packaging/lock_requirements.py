"""Hash-pinned lock files for the PC application (review finding SWR-27): every direct **and transitive** package
with its exact version and the SHA-256 of the exact wheel, installable with ``pip install --require-hashes``.

==========================  ===========================  ==============================================
input (direct pins)         lock                         use
==========================  ===========================  ==============================================
``requirements.txt``        ``requirements.lock.txt``    runtime venv (lab PC from source)
``requirements-dev.txt``    ``requirements-dev.lock.txt``  development / test venv
``requirements-build.txt``  ``requirements-build.lock.txt``  distribution build (``build_dist.ps1``)
==========================  ===========================  ==============================================

The resolution is pip's own (``pip install --dry-run --ignore-installed --report``) for the **supported platform**
(Windows 10 x64, CPython 3.14, D-33 i — the interpreter running this script); each entry carries the hash of the file
pip selected (``download_info.archive_info.hashes``), i.e. the same artefact on every installation. The equivalent of
``pip-compile --generate-hashes`` without an extra tool (KD-11)::

    .venv\\Scripts\\python 03_SW\\packaging\\lock_requirements.py generate     # re-lock after a pin change
    .venv\\Scripts\\python 03_SW\\packaging\\lock_requirements.py check        # structure + direct pins (offline)
    .venv\\Scripts\\python -m pip install --require-hashes -r 03_SW\\requirements.lock.txt   # install

Implements: SW-PLT-001 (reproducible installation from documented requirements files)
"""
from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent
SW_ROOT = PACKAGING.parent
PAIRS = (("requirements.txt", "requirements.lock.txt"), ("requirements-dev.txt", "requirements-dev.lock.txt"),
         ("requirements-build.txt", "requirements-build.lock.txt"))
_ENTRY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


@dataclass(frozen=True)
class LockEntry:
    name: str                  # normalised
    version: str
    hashes: tuple[str, ...]


def direct_pins(req: Path) -> dict[str, str]:
    """``name==version`` of a requirements file, following ``-r`` (same rules as ``buildinfo.read_pins``)."""
    pins: dict[str, str] = {}
    for raw in req.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r"):
            pins.update(direct_pins((req.parent / line[2:].strip()).resolve()))
            continue
        m = _ENTRY.match(line.replace(" ", ""))
        if m is None:
            raise ValueError(f"{req.name}: {line!r} is not pinned (name==version)")
        pins[norm(m.group(1))] = m.group(2)
    return pins


def parse_lock(text: str) -> list[LockEntry]:
    """Entries of a lock file (``name==version \\`` followed by ``--hash=sha256:…`` continuation lines)."""
    entries: list[LockEntry] = []
    cur: tuple[str, str] | None = None
    hashes: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _ENTRY.match(line)
        if m:
            if cur is not None:
                entries.append(LockEntry(cur[0], cur[1], tuple(hashes)))
            cur, hashes = (norm(m.group(1)), m.group(2)), []
        hashes += _HASH.findall(line)
    if cur is not None:
        entries.append(LockEntry(cur[0], cur[1], tuple(hashes)))
    return entries


def resolve(req: Path, python: str = sys.executable) -> list[LockEntry]:
    """pip's resolution of ``req`` for this interpreter / platform, with the hash of each selected file."""
    with tempfile.TemporaryDirectory(prefix="bbs-lock-") as tmp:
        report = Path(tmp) / "report.json"
        cmd = [python, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet",
               "--disable-pip-version-check", "--only-binary=:all:", "--report", str(report), "-r", str(req)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"pip resolution of {req.name} failed:\n{r.stderr.strip()}")
        data = json.loads(report.read_text(encoding="utf-8"))
    out = []
    for item in data["install"]:
        meta, dl = item["metadata"], item["download_info"]
        hashes = (dl.get("archive_info") or {}).get("hashes") or {}
        sha = hashes.get("sha256")
        if not sha:
            h = (dl.get("archive_info") or {}).get("hash", "")
            sha = h.split("=", 1)[1] if h.startswith("sha256=") else None
        if not sha:
            raise RuntimeError(f"{meta['name']} {meta['version']}: pip reported no sha256 for {dl.get('url')}")
        out.append(LockEntry(norm(meta["name"]), meta["version"], (sha,)))
    return sorted(out, key=lambda e: e.name)


def render(entries: list[LockEntry], source: str) -> str:
    head = [f"# Hash-pinned lock of {source} (SWR-27), generated by 03_SW/packaging/lock_requirements.py - do not edit.",
            f"# Platform: {platform.system()} {platform.machine()}, CPython {platform.python_version()} (D-33 i). "
            "Every direct and transitive package, exact version, SHA-256 of the exact wheel.",
            f"# Install: .venv\\Scripts\\python -m pip install --require-hashes -r 03_SW\\{source[:-4]}.lock.txt",
            ""]
    body = []
    for e in entries:
        body.append(f"{e.name}=={e.version} \\")
        body += [f"    --hash=sha256:{h}" + (" \\" if i < len(e.hashes) - 1 else "") for i, h in enumerate(e.hashes)]
    return "\n".join(head + body) + "\n"


def check(sw_root: Path = SW_ROOT) -> list[str]:
    """Offline consistency: every lock exists, every entry has a hash, every direct pin is locked at its version,
    and a lock that includes another requirements file (``-r``) is a superset of that file's lock."""
    problems: list[str] = []
    locks: dict[str, dict[str, LockEntry]] = {}
    for src, dst in PAIRS:
        lp = sw_root / dst
        if not lp.is_file():
            problems.append(f"{dst} missing")
            continue
        entries = parse_lock(lp.read_text(encoding="utf-8"))
        by = {e.name: e for e in entries}
        locks[src] = by
        problems += [f"{dst}: {e.name} has no sha256 hash" for e in entries if not e.hashes]
        for name, ver in direct_pins(sw_root / src).items():
            if name not in by:
                problems.append(f"{dst}: direct requirement {name} not locked")
            elif by[name].version != ver:
                problems.append(f"{dst}: {name} locked {by[name].version}, pinned {ver} in {src}")
    if "requirements.txt" in locks:
        for src in ("requirements-dev.txt", "requirements-build.txt"):
            for name, e in locks["requirements.txt"].items():
                other = locks.get(src, {}).get(name)
                if src in locks and (other is None or other.version != e.version):
                    problems.append(f"{src[:-4]}.lock.txt: runtime package {name} {e.version} differs")
    return problems


def generate(sw_root: Path = SW_ROOT) -> list[Path]:
    written = []
    for src, dst in PAIRS:
        entries = resolve(sw_root / src)
        pins = direct_pins(sw_root / src)
        by = {e.name: e for e in entries}
        bad = [f"{n}: resolved {by[n].version if n in by else 'missing'}, pinned {v}" for n, v in pins.items()
               if n not in by or by[n].version != v]
        if bad:
            raise RuntimeError(f"{src}: resolution differs from the pins: {bad}")
        (sw_root / dst).write_text(render(entries, src), encoding="utf-8", newline="\n")
        written.append(sw_root / dst)
        print(f"{dst}: {len(entries)} packages ({len(pins)} direct)")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lock_requirements.py")
    ap.add_argument("cmd", choices=("generate", "check"))
    args = ap.parse_args(argv)
    if args.cmd == "generate":
        generate()
    problems = check()
    for p in problems:
        print(f"lock: {p}", file=sys.stderr)
    print(f"lock check: {len(PAIRS)} lock files, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

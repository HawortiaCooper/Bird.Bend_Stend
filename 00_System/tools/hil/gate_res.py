"""Gate runner resources and identity (D-57): one place that knows where the runner's data files live, both in the
repository (source run by Validator E / the twin rehearsal) and in the frozen bundle (BirdBendStand-GateRunner.exe
in the gate kit, PyInstaller one-folder or one-file).

Owner: Validator E (00_System/tools/hil, the Integrator reviews). No I/O except reading the runner's own files.

Bundle layout (relative to the bundle root, `sys._MEIPASS` when frozen):
    resources/params.yaml        copy of 00_System/specs/params.yaml of the approved commit (dict hash, R-HIL-01)
    resources/pictures.json      {"<ITEM>:<KEY>": "<file>.png", ...} prompt pictures (drawn by A)
    resources/pictures/*.png     the pictures
    fw_twin/fw_twin.exe          the FW twin of the same commit (rehearsal only, --twin)
In the repository the same names resolve to the specs / tools folders; `fw_twin.exe` is then built per run.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

RUNNER_NAME = "BirdBendStand-GateRunner"
RUNNER_VERSION = "1.2.1"

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", HERE)) if FROZEN else HERE
REPO = None if FROZEN else TOOLS.parents[1]

#: source files whose content defines the runner when it runs from the repository (runner identity, D-57 b)
RUNNER_SOURCES = ("hil/gate_runner.py", "hil/gate_res.py", "hil/gate_approval.py", "hil/gate_export.py",
                  "hil/hil_session.py", "hil/hil_procs.py", "hil/hil_link.py", "hil/hil_budget.py",
                  "hil/hil_operator.py", "hil/hil_report.py", "ref_codec.py", "gen_params.py", "gen_protocol.py",
                  "fw_twin/twin.py", "fw_twin/build.py", "hil/resources/pictures.json",
                  "hil/resources/prompts_plain.json")


def resource(rel: str) -> Path:
    """A bundled resource (frozen) or its repository counterpart (source run)."""
    if FROZEN:
        return BUNDLE / rel
    if rel == "resources/params.yaml":
        return REPO / "00_System" / "specs" / "params.yaml"
    return HERE / rel


def params_yaml() -> Path:
    return resource("resources/params.yaml")


def twin_exe_default() -> Path | None:
    """Frozen: the bundled twin of the approved commit; source run: None (built per run)."""
    p = BUNDLE / "fw_twin" / "fw_twin.exe"
    return p if FROZEN and p.is_file() else None


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_sha256(exe: Path, bundle_root: Path | None) -> str:
    """Frozen runner hash (C-GR-3): the executable AND every file below the bundle root (`_internal` of the
    one-folder build: Python runtime, libraries, base_library.zip, resources/, fw_twin/ — everything the runner can
    load), path + content, sorted. `--version` (at run time, bundle root = sys._MEIPASS) and
    `make_approval.py --runner-exe` (bundle root = `_internal` next to the executable) compute the same value."""
    h = hashlib.sha256()
    h.update(b"exe\0" + sha256_file(Path(exe)).encode() + b"\0")
    if bundle_root is not None:
        root = Path(bundle_root)
        for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(root).as_posix()):
            if p.is_file():
                h.update(p.relative_to(root).as_posix().encode() + b"\0" + sha256_file(p).encode() + b"\0")
    return h.hexdigest()


def bundle_root_of(exe: Path) -> Path:
    """Bundle root of the one-folder PyInstaller build next to the executable (`_internal`, PyInstaller ≥ 6). A
    one-file build is not supported (its bundle exists only unpacked at run time): refused."""
    d = Path(exe).parent / "_internal"
    if not d.is_dir():
        raise FileNotFoundError(f"{d}: not a one-folder gate runner build (the _internal folder is missing)")
    return d


def runner_identity() -> dict:
    """Runner version and hash — what the approval file pins (D-57 b): frozen = bundle_sha256(executable, bundle
    data); source = SHA-256 over the runner's source files (name + content, fixed order, line ends normalised) and
    the picture map."""
    if FROZEN:
        exe = Path(sys.executable)
        return {"name": RUNNER_NAME, "version": RUNNER_VERSION, "kind": "frozen", "sha256": bundle_sha256(exe, BUNDLE),
                "file": exe.name}
    h = hashlib.sha256()
    for rel in RUNNER_SOURCES:
        p = TOOLS / rel
        h.update(rel.encode() + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") if p.is_file() else b"<missing>")
        h.update(b"\0")
    return {"name": RUNNER_NAME, "version": RUNNER_VERSION, "kind": "source", "sha256": h.hexdigest(),
            "file": "00_System/tools (" + ", ".join(RUNNER_SOURCES) + ")"}


def pictures() -> dict[str, list[Path]]:
    """Prompt pictures {"ITEM:KEY" | "*:KEY" | "ITEM:*": [paths]} that exist (missing pictures are not shown)."""
    f = resource("resources/pictures.json")
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pic_dir = f.parent / "pictures" if FROZEN else REPO / "01_HW" / "diagrams" / "bench"
    out: dict[str, list[Path]] = {}
    for k, v in m.items():
        if k.startswith("_"):
            continue
        ps = [pic_dir / str(x) for x in (v if isinstance(v, list) else [v])]
        ps = [p for p in ps if p.is_file()]
        if ps:
            out[str(k)] = ps
    return out


def pictures_for(pics: dict[str, list[Path]], item: str, key: str) -> list[Path]:
    """Most specific first: ITEM:KEY, then *:KEY, then ITEM:* (GR3-1)."""
    for k in (f"{item}:{key}", f"*:{key}", f"{item}:*"):
        if k in pics:
            return pics[k]
    return []


def plain_prompts() -> dict[str, str]:
    """GR-2 plain-language operator texts {"ITEM:KEY" | "*:KEY": text}; the technical prompt is shown below it."""
    try:
        m = json.loads(resource("resources/prompts_plain.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in m.items() if not str(k).startswith("_")}


def plain_for(plain: dict[str, str], item: str, key: str) -> str | None:
    return plain.get(f"{item}:{key}") or plain.get(f"*:{key}")

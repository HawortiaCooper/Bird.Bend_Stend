"""Gate runner results zip (D-57 c): one file the operator sends to the PO; Validator E analyses it offline
(`hil_analyze.py`). Written only into the session's own output folder (standing data rule).

Owner: Validator E (00_System/tools/hil, the Integrator reviews). Layout: GATE_RUNNER.md §4.

    gate_<YYYYMMDD>_<board UID | twin>_<session id 8>.zip
      manifest.json      schema bird.bend.gate_results v1: session id, mode, board UID, runner identity, approval
                         reference + body hash, kit image hashes, created (UTC), and {path, size, sha256} of every
                         other member
      session.json       session metadata (hil_session)
      results/<id>.json  one per HG / session item: checks (value, budget, u, decision), raw data, operator answers
      HG_report_<mode>.md
      prompts.jsonl      every prompt answer with UTC time stamp (operator / twin source)
      raw/frames.bin     raw frame log, both directions, time-stamped (hil_link.LoggedTransport)
      approval.json      the approval file the session ran under (target sessions)
      runner.json        runner identity, Python, OS, command line, verified kit hashes
      photos/…           files the operator copied into <out>/photos (optional)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import zipfile
from pathlib import Path

SCHEMA = "bird.bend.gate_results"
SCHEMA_VERSION = 1
MEMBERS = ("session.json", "prompts.jsonl", "approval.json", "runner.json")
DIRS = ("results", "raw", "photos")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def collect(out: Path) -> list[tuple[str, Path]]:
    """(archive name, file) of every member taken from the session folder — nothing outside `out`."""
    out = Path(out).resolve()
    files: list[tuple[str, Path]] = []
    for name in MEMBERS:
        p = out / name
        if p.is_file():
            files.append((name, p))
    for p in sorted(out.glob("HG_report_*.md")):
        files.append((p.name, p))
    for d in DIRS:
        root = out / d
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                rp = p.resolve()
                if p.is_file() and not p.is_symlink() and out in rp.parents:
                    files.append((p.relative_to(out).as_posix(), p))
    return files


def zip_name(meta: dict) -> str:
    day = str(meta.get("date", ""))[:10].replace("-", "") or _dt.date.today().strftime("%Y%m%d")
    who = meta.get("board_uid") or meta.get("mode", "session")
    return f"gate_{day}_{who}_{str(meta.get('session_id', 'x'))[:8]}.zip"


def export(out: Path) -> Path:
    """Write the results zip into `out` (atomic: temp name, then rename). Returns its path."""
    out = Path(out)
    meta = json.loads((out / "session.json").read_text(encoding="utf-8"))
    try:
        runner = json.loads((out / "runner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        runner = {}
    entries = []
    blobs: list[tuple[str, bytes]] = []
    for name, p in collect(out):
        b = p.read_bytes()
        blobs.append((name, b))
        entries.append({"path": name, "size": len(b), "sha256": _sha(b)})
    manifest = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
                "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                "session_id": meta.get("session_id"), "mode": meta.get("mode"), "board_uid": meta.get("board_uid"),
                "approval": meta.get("approval"), "approval_sha256": meta.get("approval_sha256"),
                "runner": runner.get("runner"), "kit": runner.get("kit"), "files": entries}
    dst = out / zip_name(meta)
    tmp = dst.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, b in blobs:
            z.writestr(name, b)
        z.writestr("manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False))
    os.replace(tmp, dst)
    return dst


def verify(zip_path: Path) -> list[str]:
    """Problems of a results zip (empty = intact): manifest present and well-formed, every listed member present with
    its size and SHA-256, no unlisted member, no path outside the archive root."""
    problems: list[str] = []
    try:
        z = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as e:
        return [f"not a zip: {e}"]
    with z:
        names = set(z.namelist())
        if "manifest.json" not in names:
            return ["manifest.json missing"]
        try:
            m = json.loads(z.read("manifest.json"))
        except ValueError as e:
            return [f"manifest.json unreadable: {e}"]
        if m.get("schema") != SCHEMA or m.get("schema_version") != SCHEMA_VERSION:
            problems.append(f"manifest schema {m.get('schema')!r} v{m.get('schema_version')!r}")
        listed = set()
        for e in m.get("files", []):
            n = str(e.get("path", ""))
            listed.add(n)
            if n.startswith(("/", "\\")) or ".." in Path(n).parts:
                problems.append(f"{n}: path outside the archive")
                continue
            if n not in names:
                problems.append(f"{n}: listed but missing")
                continue
            b = z.read(n)
            if len(b) != e.get("size") or _sha(b) != e.get("sha256"):
                problems.append(f"{n}: size / SHA-256 differ from the manifest")
        for n in sorted(names - listed - {"manifest.json"}):
            problems.append(f"{n}: not listed in the manifest")
    return problems

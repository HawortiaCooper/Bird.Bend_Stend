#!/usr/bin/env python3
"""make_approval — the Orchestrator's tool that turns the PO's recorded D-06 gate approval into the gate runner's
approval file (D-57 b). Owner: Validator E (the Integrator reviews); run by the Orchestrator in the repository.

    .venv\\Scripts\\python 00_System\\tools\\hil\\make_approval.py --ref D-06-GATE-20261020 \\
        --board-uid 3400451A3133510237363934 --runner-exe <GateKit>\\BirdBendStand-GateRunner\\BirdBendStand-GateRunner.exe \\
        [--accepted-open HG-12:RR-HG-12-01 ...] [--valid-days 7] [--delivery 04_Delivery] --out approval_<ref>.json

What it checks, with the repository rules of hil_link (C-R1…C-R4, FWR-12/13): the reference is a dedicated approved
row in DECISIONS.md / STATUS.md, dated today or up to 7 days ago, not revoked by any other row, and the row names the
board UID; each accepted-open item has its own accepted row naming the item; the three .hex files (release in
BringUp_FW…/firmware, HW_MEAS / HW_MEAS_DWT in GateKit_FW…) are listed in their kit's SHA256SUMS.txt; build strings,
commit, dictionary hash and the TC-SYS-009-02 result come from the kits' BUILD_INFO.txt; the dictionary hash equals
params.yaml. Output: the sealed JSON (gate_approval.seal) and a ready-to-paste row for
00_System/specs/GATE_APPROVAL_LOG.md (GR-5: issued UTC | body SHA-256 | D-06-GATE ref | board UID | kit commit |
runner hash | issued for). The log's first cell is the issue time, never the reference, and the approval-row parser
reads only DECISIONS.md / STATUS.md (hil_link.APPROVAL_FILES), so a log row is never an approval record (C-R1).
Validator E checks a results zip against the log offline: hil_analyze.py --approval-log <log> (or --approval-sha).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gate_approval as ga  # noqa: E402
import gate_res  # noqa: E402
import hil_link as hl  # noqa: E402
from hil_link import InterlockError  # noqa: E402

TOOL_VERSION = "1.0"
PATTERNS = {"release": "BringUp_FW*/firmware/*_release.hex",
            "meas": "GateKit_FW*/*_HW_MEAS_GATE-ONLY.hex",
            "meas_dwt": "GateKit_FW*/*_HW_MEAS_DWT_GATE-ONLY.hex"}
ENV_OF = {"release": "nucleo_f446re", "meas": "nucleo_f446re_meas", "meas_dwt": "nucleo_f446re_meas_dwt"}


def parse_build_info(text: str) -> dict:
    """{env: build}, commit, dict hash and the TC-SYS-009-02 line of a kit's BUILD_INFO.txt."""
    out: dict = {"builds": {}}
    env = None
    for line in text.splitlines():
        m = re.search(r"\benv\s+(\S+)", line)
        if line.startswith("[") and m:
            env = m.group(1)
            continue
        m = re.match(r"\s*GET_INFO build\s*:\s*(\S+)", line)
        if m and env:
            out["builds"][env] = m.group(1)
        m = re.match(r"\s*Git commit\s*:\s*([0-9a-f]{7,40})", line)
        if m:
            out["commit"] = m.group(1)
        m = re.match(r"\s*GET_INFO fw_version \(wire\)\s*:\s*(\S+)", line)
        if m:
            out["fw_version"] = m.group(1)
        m = re.match(r"\s*PARAM_DICT_HASH[^:]*:\s*(0x[0-9A-Fa-f]{8})", line)
        if m:
            out["dict_hash"] = m.group(1)
        if "TC-SYS-009-02" in line and "check_meas_build" in line:
            out["tc_sys_009_02"] = line.strip()
    return out


def find_image(delivery: Path, name: str, explicit: str | None) -> Path:
    if explicit:
        p = (delivery / explicit) if not Path(explicit).is_absolute() else Path(explicit)
        if not p.is_file():
            raise InterlockError(f"{name}: {p} not found")
        return p
    hits = sorted(delivery.glob(PATTERNS[name]))
    if len(hits) != 1:
        raise InterlockError(f"{name}: expected exactly one {PATTERNS[name]} under {delivery}, found {len(hits)}")
    return hits[0]


def listed_sha(hexfile: Path) -> str:
    """The file's SHA-256, which must also be the one in its kit's SHA256SUMS.txt."""
    sha = gate_res.sha256_file(hexfile)
    for d in (hexfile.parent, hexfile.parent.parent):
        sums = d / "SHA256SUMS.txt"
        if sums.is_file():
            for line in sums.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("*").replace("\\", "/").endswith(hexfile.name):
                    if parts[0].lower() != sha:
                        raise InterlockError(f"{hexfile.name}: SHA-256 differs from {sums}")
                    return sha
    raise InterlockError(f"{hexfile.name}: not listed in a SHA256SUMS.txt of its kit")


def build(ref: str, uid: str, delivery: Path, runner_sha: str, accepted: list[str], valid_days: int,
          today: _dt.date | None = None, images: dict[str, str | None] | None = None, repo: Path = hl.REPO,
          by: str | None = None, options: list[str] | None = None) -> dict:
    uid = uid.strip().upper()
    if not hl.UID_RE.match(uid):
        raise InterlockError(f"--board-uid {uid!r} is not 24 hex digits")
    d0 = hl.check_approval(ref, today=today, repo=repo)                       # dedicated approved row, ≤ 7 days
    row = hl.recorded_in_files(ref, hl.APPROVED_STATES, must_mention=uid, repo=repo)
    if not row:
        raise InterlockError(f"{ref}: the approved row does not name the board UID {uid}")
    if not 0 <= valid_days <= ga.MAX_SPAN_DAYS:
        raise InterlockError(f"--valid-days must be 0…{ga.MAX_SPAN_DAYS}")
    acc = []
    for x in accepted:
        item, _, aref = x.partition(":")
        arow = hl.check_accepted_open(item.strip(), aref.strip(), repo=repo)
        acc.append({"item": item.strip(), "ref": aref.strip(), "row": arow})
    imgs, infos = {}, {}
    for name in ("release", "meas", "meas_dwt"):
        f = find_image(delivery, name, (images or {}).get(name))
        bi_path = next((p / "BUILD_INFO.txt" for p in (f.parent, f.parent.parent) if (p / "BUILD_INFO.txt").is_file()),
                       None)
        if bi_path is None:
            raise InterlockError(f"{name}: no BUILD_INFO.txt in its kit")
        bi = parse_build_info(bi_path.read_text(encoding="utf-8"))
        infos[name] = bi
        b = bi["builds"].get(ENV_OF[name])
        if not b:
            raise InterlockError(f"{name}: BUILD_INFO.txt names no GET_INFO build for env {ENV_OF[name]}")
        imgs[name] = {"path": f.resolve().relative_to(delivery.resolve()).as_posix(), "sha256": listed_sha(f),
                      "build": b}
    commits = {bi.get("commit") for bi in infos.values()}
    if len(commits) != 1 or None in commits:
        raise InterlockError(f"the kits name different / no commits: {sorted(map(str, commits))}")
    hashes = {str(bi.get("dict_hash", "")).upper() for bi in infos.values()}
    if hashes != {hl.DICT_HASH.upper()}:
        raise InterlockError(f"kit dictionary hash {sorted(hashes)} != params.yaml {hl.DICT_HASH}")
    tc = next((bi["tc_sys_009_02"] for bi in infos.values() if bi.get("tc_sys_009_02")), "")
    evidence = {"tc_sys_009_02": ("PASS — " + tc) if "PASS" in tc else ("NOT RECORDED — " + tc)}
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = ""
    doc = {"schema": ga.SCHEMA, "schema_version": ga.SCHEMA_VERSION, "reference": ref, "decision_row": row,
           "board_uid": uid, "valid_from": d0.isoformat(), "valid_until": (d0 + _dt.timedelta(days=valid_days)).isoformat(),
           "kit": {"name": ", ".join(sorted({Path(v["path"]).parts[0] for v in imgs.values()})), "commit": commits.pop(),
                   "fw_version": infos["release"].get("fw_version", ""), "param_dict_hash": hl.DICT_HASH,
                   "images": imgs},
           "runner": {"name": gate_res.RUNNER_NAME, "version": gate_res.RUNNER_VERSION, "sha256": runner_sha.lower()},
           "accepted_open": acc, "evidence": evidence,
           **({"options": dict(ga.parse_option(o) for o in options)} if options else {}),
           "generated_by": {"tool": "make_approval.py", "version": TOOL_VERSION,
                            "utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                            "repo_commit": head, "by": by or getpass.getuser()}}
    return ga.seal(doc)


def log_row(doc: dict, issued_for: str) -> str:
    """GATE_APPROVAL_LOG.md row (GR-5) — first cell = issue time (never the reference, C-R1)."""
    cells = [doc["generated_by"]["utc"], doc["body_sha256"], doc["reference"], doc["board_uid"], doc["kit"]["commit"],
             doc["runner"]["sha256"], (issued_for or "").replace("|", "/").strip() or "-"]
    return "| " + " | ".join(str(c) for c in cells) + " |"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--board-uid", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--runner-exe", type=Path, help="the frozen runner of the gate kit (hashed)")
    g.add_argument("--runner-sha", help="SHA-256 of the runner (e.g. 'source' run by Validator E: gate_runner.py --version)")
    ap.add_argument("--accepted-open", action="append", default=[], metavar="HG-xx:REF")
    ap.add_argument("--valid-days", type=int, default=ga.MAX_SPAN_DAYS)
    ap.add_argument("--delivery", type=Path, default=(hl.REPO / "04_Delivery"))
    ap.add_argument("--by", help="name recorded in generated_by")
    ap.add_argument("--issued-for", default="", help="free text for the log row (e.g. 'HG1 session, operator N.N.')")
    ap.add_argument("--option", action="append", default=[], metavar="KEY=VALUE",
                    help="pin a session option for the board session (C-GR-1; written only when given): "
                         "quick, seed, counts_per_n, buffer_fitted, power_sense_fitted")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        sha = (gate_res.bundle_sha256(a.runner_exe, gate_res.bundle_root_of(a.runner_exe)) if a.runner_exe
               else str(a.runner_sha))
    except OSError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    if not ga.SHA_RE.match(sha.lower()):
        print("REFUSED: runner SHA-256 is not 64 hex digits", file=sys.stderr)
        return 2
    try:
        doc = build(a.ref, a.board_uid, a.delivery, sha, a.accepted_open, a.valid_days, by=a.by, options=a.option)
    except InterlockError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    import json  # noqa: PLC0415
    a.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"approval file: {a.out}\nbody_sha256: {doc['body_sha256']}\n"
          "Append this row to 00_System/specs/GATE_APPROVAL_LOG.md (GR-5; never edit an existing row):\n"
          + log_row(doc, a.issued_for or (a.by or "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""hil_analyze — Validator E's offline analysis of a gate runner results zip (D-57 c).

    .venv\\Scripts\\python 00_System\\tools\\hil\\hil_analyze.py <results zip>
        [--approval-log 00_System\\specs\\GATE_APPROVAL_LOG.md | --approval-sha <recorded body hash>]
        [--runner-sha <expected runner hash>] [--out FW_test_report_HG1_draft.md]

Owner: Validator E (00_System/tools/hil, the Integrator reviews). Reads only the zip; writes only --out.
Steps:
  1. integrity: manifest (every member listed with size + SHA-256, nothing unlisted, gate_export.verify);
  2. provenance: approval.json body hash recomputed = manifest = the hash the Orchestrator recorded (--approval-sha);
     runner hash in the manifest = the approval's; kit images and builds = what S-00 / the IMG steps saw;
  3. re-evaluation: every check of every item is decided again from its recorded value, budget and uncertainty with
     the §6.1 rule-4 functions of hil_budget (upper / lower budget, range; DWT sections from the recorded section
     data); a check whose recorded decision differs is listed;
  4. raw frame log: parsed with the reference codec (frames per direction, CRC / length errors, time order);
  5. operator answers: sources (a board session must contain operator answers only, never dry-run defaults);
  6. a draft of the FW_test_report_HG1 tables (per item: verdict, deciding checks with value / budget / u, the
     operator-attested items, re-evaluation status).
Exit code 0 = intact and re-evaluation agrees, 1 = a problem is listed.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import gate_approval as ga  # noqa: E402
import gate_export  # noqa: E402
import gate_res  # noqa: E402
import hil_budget as hb  # noqa: E402
import hil_link as hl  # noqa: E402
import ref_codec as rc  # noqa: E402

DECIDED = (hb.PASS, hb.FAIL, hb.INCONCL)


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def reevaluate(c: dict) -> tuple[str | None, str]:
    """(recomputed decision or None if not re-evaluable, how)."""
    crit, dec = str(c.get("criterion", "")), c.get("decision")
    if dec not in DECIDED:
        return None, "not a measured decision"
    u = _num(c.get("u")) or 0.0
    v, b = c.get("value"), c.get("budget")
    st = c.get("stats") or {}
    if "section" in crit and "stamp overhead" in crit:                    # DWT (hb.check_dwt)
        nv, nb = _num(v), _num(b)
        if nv is None or nb is None:
            return None, "DWT without value"
        m = re.search(r"raw max ([0-9.]+) µs", str(c.get("note", "")))
        raw = float(m.group(1)) if m else None
        d = hb.PASS if raw is not None and raw <= nb else hb.decide_le(nv, nb, u)
        return d, "DWT: raw max ≤ budget or net + u ≤ budget"
    if "<= value ± u <=" in crit and isinstance(v, (list, tuple)) and isinstance(b, (list, tuple)):
        lo, hi = float(b[0]), float(b[1])
        dl, dh = hb.decide_ge(float(v[0]), lo, u), hb.decide_le(float(v[1]), hi, u)
        return (hb.FAIL if hb.FAIL in (dl, dh) else hb.INCONCL if hb.INCONCL in (dl, dh) else hb.PASS), "range"
    if "+ u <=" in crit and _num(v) is not None and _num(b) is not None:
        if st.get("max") is not None and abs(float(st["max"]) - float(v)) > 1e-9 * max(1.0, abs(float(v))):
            return hb.FAIL, "value != stats max"
        return hb.decide_le(float(v), float(b), u), "upper budget"
    if "- u >=" in crit and _num(v) is not None and _num(b) is not None:
        if st.get("min") is not None and abs(float(st["min"]) - float(v)) > 1e-9 * max(1.0, abs(float(v))):
            return hb.FAIL, "value != stats min"
        return hb.decide_ge(float(v), float(b), u), "lower limit"
    if crit.startswith("=="):
        if isinstance(v, (int, float)) and isinstance(b, (int, float)):
            return (hb.PASS if abs(v - b) <= u else hb.FAIL), "equality"
        return (hb.PASS if v == b else hb.FAIL), "equality"
    return None, "boolean / count criterion (recorded decision kept)"


def analyse(zip_path: Path, approval_sha: str | None = None, runner_sha: str | None = None,
            zip_sha: str | None = None, approval_log: list[dict] | None = None) -> dict:
    """Everything a board session must satisfy is a PROBLEM when it does not hold (C-GR-2): a target zip needs both
    anchors - the approval body hash from the Orchestrator's log (approval_log / approval_sha) and the results-zip
    hash the operator sent separately (zip_sha) - and only default or approval-pinned options, never --quick."""
    rep: dict[str, Any] = {"zip": str(zip_path), "problems": [], "mismatches": [], "items": [], "raw": {},
                           "prompts": {}, "notes": []}
    rep["problems"] += gate_export.verify(zip_path)
    got_zip_sha = gate_res.sha256_file(Path(zip_path))
    rep["zip_sha256"] = got_zip_sha
    if zip_sha and zip_sha.strip().lower() != got_zip_sha:
        rep["problems"].append(f"results zip SHA-256 {got_zip_sha[:16]}… != the line the operator sent "
                               f"({zip_sha.strip()[:16]}…) - the zip was changed after the session")
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        man = json.loads(z.read("manifest.json")) if "manifest.json" in names else {}
        rep["manifest"] = {k: v for k, v in man.items() if k != "files"}
        meta = json.loads(z.read("session.json")) if "session.json" in names else {}
        rep["session"] = meta
        # provenance
        if "approval.json" in names:
            ad = json.loads(z.read("approval.json"))
            sha = ga.body_sha256(ad)
            if sha != ad.get("body_sha256"):
                rep["problems"].append("approval.json: body hash does not match its content (edited)")
            if man.get("approval_sha256") != sha:
                rep["problems"].append("manifest approval hash != approval.json")
            if approval_sha and approval_sha.lower() != sha:
                rep["problems"].append(f"approval body hash {sha[:16]}… != the recorded {approval_sha[:16]}…")
            want_r = str(ad.get("runner", {}).get("sha256", ""))
            got_r = str((man.get("runner") or {}).get("sha256", ""))
            if want_r != got_r:
                rep["problems"].append("runner hash in the session != the approval's runner")
            if runner_sha and runner_sha.lower() != got_r:
                rep["problems"].append("runner hash != the expected runner")
            rep["approval"] = {k: ad.get(k) for k in ("reference", "board_uid", "valid_from", "valid_until")}
            rep["approval"]["body_sha256"] = sha
            rep["approval"]["kit_commit"] = (ad.get("kit") or {}).get("commit")
            rep["approval"]["runner_sha256"] = want_r
        elif meta.get("mode") == "target":
            rep["problems"].append("board session without approval.json")
        if meta.get("mode") == "target":
            if not (approval_sha or approval_log is not None):
                rep["problems"].append("no approval anchor: run with --approval-log (or --approval-sha) - a board zip "
                                       "is never 'consistent' without it (C-GR-2)")
            if not zip_sha:
                rep["problems"].append("no results-zip anchor: run with --zip-sha <line the operator sent to the PO>")
            opts = meta.get("options") or {}
            pinned = set(meta.get("options_pinned") or [])
            if meta.get("quick") or opts.get("quick"):
                rep["problems"].append("board session run with --quick (reduced trials, never evidence)")
            for k, v in opts.items():
                if k in ga.DEFAULT_OPTIONS and v != ga.DEFAULT_OPTIONS[k] and k != "quick":
                    if k in pinned:
                        rep["notes"].append(f"option {k} = {v} (pinned by the approval file)")
                    else:
                        rep["problems"].append(f"option {k} = {v} differs from the default and is not pinned by "
                                               "the approval file (C-GR-1)")
            if not opts:
                rep["problems"].append("session.json records no options (runner older than C-GR-1?)")
        # items
        for n in sorted(x for x in names if x.startswith("results/") and x.endswith(".json")):
            it = json.loads(z.read(n))
            entry = {"id": it.get("id"), "title": it.get("title"), "verdict": it.get("verdict"),
                     "error": it.get("error"), "checks": [], "attested": []}
            for c in it.get("checks", []):
                d, how = reevaluate(c)
                entry["checks"].append({**{k: c.get(k) for k in ("name", "criterion", "decision", "value", "unit",
                                                                  "u", "budget", "n")}, "re": d, "how": how})
                if d is not None and d != c.get("decision"):
                    rep["mismatches"].append(f"{it.get('id')}: {c.get('name')}: recorded {c.get('decision')}, "
                                             f"re-evaluated {d} ({how})")
            for a in it.get("operator", []) or []:
                entry["attested"].append(f"{a.get('key')}: {a.get('value')} ({a.get('source')})")
            rep["items"].append(entry)
            sid = (it.get("session") or {}).get("session_id") if isinstance(it.get("session"), dict) else it.get("session")
            if meta.get("session_id") and sid != meta["session_id"]:
                rep["problems"].append(f"{n}: result of another session ({sid})")
            if isinstance(it.get("session"), dict) and meta.get("board_uid") and \
                    it["session"].get("board_uid") != meta.get("board_uid"):
                rep["problems"].append(f"{n}: result of another board")
        # raw frames
        if "raw/frames.bin" in names:
            data = z.read("raw/frames.bin")
            tmp = io.BytesIO(data)
            cnt = {0: 0, 1: 0}
            parsers = {0: rc.FrameParser(), 1: rc.FrameParser()}
            last_t = -1
            back = 0
            for t, d, b in _iter_raw(tmp.getvalue()):
                if t < last_t:
                    back += 1
                last_t = t
                cnt[d] = cnt.get(d, 0) + len(parsers[d].feed(b))
            rep["raw"] = {"frames_pc_to_fw": cnt[0], "frames_fw_to_pc": cnt[1],
                          "crc_errors": parsers[0].crc_errors + parsers[1].crc_errors,
                          "len_errors": parsers[0].len_errors + parsers[1].len_errors, "time_reversals": back}
            if back and meta.get("mode") == "target":          # PC clock: monotonic on the bench
                rep["problems"].append(f"raw frame log: {back} time reversals")
            elif back:
                rep["raw"]["note"] = "twin: virtual time restarts with each emulated re-flash"
        elif meta.get("mode") == "target":
            rep["problems"].append("board session without raw/frames.bin")
        # prompts
        if "prompts.jsonl" in names:
            src: dict[str, int] = {}
            for line in z.read("prompts.jsonl").decode("utf-8").splitlines():
                if line.strip():
                    s = json.loads(line).get("source", "?")
                    src[s] = src.get(s, 0) + 1
            rep["prompts"] = src
            if meta.get("mode") == "target" and set(src) - {"operator"}:
                rep["problems"].append(f"board session with non-operator answers: {sorted(set(src) - {'operator'})}")
    return rep


LOG_COLS = ("issued_utc", "body_sha256", "reference", "board_uid", "kit_commit", "runner_sha256", "issued_for")


def parse_approval_log(text: str) -> list[dict]:
    """Rows of GATE_APPROVAL_LOG.md (GR-5): 7 cells, the 2nd a SHA-256; header / separator / rows inside fences,
    comments or indented blocks (hil_link.table_lines, C-R2 / C-R4) are ignored."""
    rows = []
    for line in hl.table_lines(text):
        cells = hl._cells(line)
        if cells and len(cells) == len(LOG_COLS) and ga.SHA_RE.match(cells[1].lower()):
            rows.append(dict(zip(LOG_COLS, cells)))
    return rows


def check_against_log(rep: dict, rows: list[dict]) -> None:
    """The zip's approval copy must be a logged issue with the same reference, board, kit commit and runner."""
    ap = rep.get("approval") or {}
    sha = ap.get("body_sha256")
    if not sha:
        rep["problems"].append("no approval.json in the zip to compare with the approval log")
        return
    hits = [r for r in rows if r["body_sha256"].lower() == sha]
    if not hits:
        rep["problems"].append(f"approval body {sha[:16]}… is not in the approval log (not issued by the Orchestrator)")
        return
    r = hits[-1]
    want = {"reference": ap.get("reference"), "board_uid": ap.get("board_uid"), "kit_commit": ap.get("kit_commit"),
            "runner_sha256": ap.get("runner_sha256")}
    for k, v in want.items():
        if v is not None and str(r[k]).lower() != str(v).lower():
            rep["problems"].append(f"approval log row {r['issued_utc']}: {k} {r[k]} != the approval file's {v}")
    rep["approval"]["log_row"] = r["issued_utc"]


def _iter_raw(b: bytes):
    import struct  # noqa: PLC0415
    if not b.startswith(hl.RAW_MAGIC):
        raise ValueError("not a raw frame log")
    i = len(hl.RAW_MAGIC)
    while i + 13 <= len(b):
        t, d, n = struct.unpack_from("<QBI", b, i)
        i += 13
        yield t, d, b[i:i + n]
        i += n


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, (list, tuple)):
        return "(" + ", ".join(_fmt(x) for x in v) + ")"
    s = str(v)
    return s if len(s) <= 60 else s[:57] + "…"


def draft(rep: dict) -> str:
    m, s = rep.get("manifest", {}), rep.get("session", {})
    out = ["# FW_test_report_HG1 — draft tables (hil_analyze.py)", "",
           f"Results zip `{Path(rep['zip']).name}` · session `{m.get('session_id')}` · mode {m.get('mode')} · board "
           f"{m.get('board_uid')} · approval {m.get('approval')} (body {str(m.get('approval_sha256'))[:16]}…) · runner "
           f"{(m.get('runner') or {}).get('version')} {str((m.get('runner') or {}).get('sha256'))[:16]}… · date "
           f"{s.get('date')}", "",
           "## Integrity and provenance", ""]
    out += [f"- PROBLEM: {p}" for p in rep["problems"]] or ["- manifest, hashes, approval and runner: consistent"]
    out += [f"- note: {n}" for n in rep.get("notes", [])]
    out += [f"- results zip SHA-256 {rep.get('zip_sha256')}"]
    out += [f"- raw frame log: {rep.get('raw')}", f"- operator answers by source: {rep.get('prompts')}", "",
            "## Re-evaluation (§6.1 rule 4)", ""]
    out += [f"- MISMATCH: {x}" for x in rep["mismatches"]] or ["- every measured decision re-evaluates identically"]
    out += ["", "## Items", "", "| Item | Title | Verdict | Deciding checks (value / budget, u → decision) |",
            "|---|---|---|---|"]
    for it in rep["items"]:
        ch = "; ".join(f"{c['name']}: {_fmt(c['value'])} {c.get('unit') or ''} / {_fmt(c['budget'])}"
                       + (f", u {_fmt(c['u'])}" if c.get("u") not in (None, 0) else "") + f" → {c['decision']}"
                       for c in it["checks"] if c["decision"] in DECIDED)
        verdict = it["verdict"] + (f" ({it['error']})" if it.get("error") else "")
        out.append(f"| {it['id']} | {it['title']} | {verdict} | {ch or '–'} |")
    out += ["", "## Operator-attested entries", "", "| Item | Entries |", "|---|---|"]
    for it in rep["items"]:
        if it["attested"]:
            out.append(f"| {it['id']} | " + "; ".join(_fmt(x) for x in it["attested"]) + " |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", type=Path)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--approval-sha", help="recorded approval body SHA-256")
    g.add_argument("--approval-log", type=Path, help="00_System/specs/GATE_APPROVAL_LOG.md (GR-5)")
    ap.add_argument("--zip-sha", help="the results-zip SHA-256 line the operator sent separately (design limit b)")
    ap.add_argument("--runner-sha")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    rows = parse_approval_log(a.approval_log.read_text(encoding="utf-8")) if a.approval_log else None
    zs = a.zip_sha
    if zs:
        m = re.search(r"[0-9a-fA-F]{64}", zs)
        zs = m.group(0) if m else zs
    rep = analyse(a.zip, a.approval_sha, a.runner_sha, zip_sha=zs, approval_log=rows)
    if rows is not None:
        check_against_log(rep, rows)
    md = draft(rep)
    if a.out:
        a.out.write_text(md, encoding="utf-8")
        print(f"draft: {a.out}")
    else:
        print(md)
    print(f"problems: {len(rep['problems'])}, re-evaluation mismatches: {len(rep['mismatches'])}")
    return 0 if not rep["problems"] and not rep["mismatches"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""HG report generator (markdown) from a HIL session directory (results/<item>.json + session.json).

Owner: Validator E. Output: <session>/HG_report_<mode>.md — one section per HG item / session step with method,
checks (value, uncertainty, budget, decision per FW_test_plan §6.1 rule 4), operator answers with their source,
notes and errors; a summary table; the list of what stays open (MANUAL / TARGET-ONLY / NOT MEASURED).
The final FW_test_report_HG1.md (plan §5.4) is written by Validator E with this report as its annex.

    .venv\\Scripts\\python 00_System\\tools\\hil\\hil_report.py <session dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import hil_budget as hb


def _v(x) -> str:
    if x is None:
        return "–"
    if isinstance(x, float):
        return f"{x:.4g}"
    if isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(y, (int, float)) for y in x):
        return f"{_v(x[0])} … {_v(x[1])}"
    s = str(x)
    return (s[:117] + "…") if len(s) > 120 else s


def _cell(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def load(session: Path) -> tuple[dict, list[dict]]:
    meta = json.loads((session / "session.json").read_text(encoding="utf-8")) if (session / "session.json").exists() else {}
    order = meta.get("order", [])
    res = {}
    for f in sorted((session / "results").glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        res[r["id"]] = r
    items = [res[i] for i in order if i in res] + [r for k, r in res.items() if k not in order]
    return meta, items


def render(session: Path) -> str:
    meta, items = load(session)
    mode = meta.get("mode", "?")
    out: list[str] = []
    title = "HG1 hardware gate — session report" if mode == "target" else "HG1 hardware gate — TWIN DRY RUN report"
    out += [f"# {title}", ""]
    if mode != "target":
        out += ["> **Dry run against the FW host twin (`Twin(hw_meas=True)`, Integrator's DIAG_MEAS model).** It exercises "
                "the HIL procedures, the DIAG_MEAS evidence chain and the budget evaluation. **It is not hardware "
                "evidence** — D-06 stays in force; every HG item is decided on the board at the PO-approved gate. "
                "Operator answers marked *dry-run default* are scripted placeholders; *twin-observed* values come from "
                "the twin's world model.", ""]
    out += ["| Field | Value |", "|---|---|"]
    for k in ("mode", "date", "approval", "port", "board_uid", "operator", "fw_build", "images_commit", "icd", "dict_hash",
              "python", "host", "seed", "quick", "twin_exe", "cfg_overrides"):
        if k in meta:
            out.append(f"| {k} | {_cell(_v(meta[k]))} |")
    out.append("")
    # summary
    out += ["## Summary", "", "| Item | Title | Verdict | PASS | FAIL | INCONCL. | open | Requirements |",
            "|---|---|---|---|---|---|---|---|"]
    tot: dict[str, int] = {}
    for it in items:
        ds = [c["decision"] for c in it["checks"]]
        n = {k: ds.count(k) for k in (hb.PASS, hb.FAIL, hb.INCONCL)}
        n_open = sum(1 for d in ds if d in hb.OPEN_KINDS)
        tot[it["verdict"].split(" ")[0]] = tot.get(it["verdict"].split(" ")[0], 0) + 1
        out.append(f"| {it['id']} | {_cell(it['title'])} | **{_cell(it['verdict'])}** | {n[hb.PASS]} | {n[hb.FAIL]} | "
                   f"{n[hb.INCONCL]} | {n_open} | {_cell(it.get('req', ''))} |")
    out += ["", "Verdict counts: " + ", ".join(f"{k} {v}" for k, v in sorted(tot.items())), ""]
    # open list
    openl = [(it["id"], c) for it in items for c in it["checks"] if c["decision"] in hb.OPEN_KINDS]
    if openl:
        out += ["## What stays open after this run", "", "| Item | Check | Kind | Why |", "|---|---|---|---|"]
        for iid, c in openl:
            out.append(f"| {iid} | {_cell(c['name'])} | {c['decision']} | {_cell(_v(c.get('note', '')))} |")
        out.append("")
    # details
    out += ["## Items", ""]
    for it in items:
        out += [f"### {it['id']} — {it['title']}", "",
                f"Verdict: **{it['verdict']}** · requirements: {it.get('req', '–') or '–'} · {it.get('t_start', '')} → "
                f"{it.get('t_end', '')}", ""]
        if it.get("method"):
            out += [f"Method: {it['method']}", ""]
        if it.get("error"):
            out += [f"**Error:** `{_cell(it['error'])}`", ""]
        if it["checks"]:
            out += ["| Check | Criterion | Value | u | n | Decision | Note |", "|---|---|---|---|---|---|---|"]
            for c in it["checks"]:
                val = _v(c.get("value"))
                if c.get("unit") and c.get("value") is not None and not isinstance(c.get("value"), (str, bool, list, dict)):
                    val += f" {c['unit']}"
                out.append(f"| {_cell(c['name'])} | {_cell(c['criterion'])} | {_cell(val)} | {_v(c.get('u'))} | "
                           f"{_v(c.get('n'))} | **{c['decision']}** | {_cell(_v(c.get('note', '')))} |")
            out.append("")
        ops = it.get("operator", [])
        if ops:
            out += ["<details><summary>Operator steps / answers (" + str(len(ops)) + ")</summary>", "",
                    "| Key | Prompt | Answer | Source |", "|---|---|---|---|"]
            for o in ops:
                out.append(f"| {o['key']} | {_cell(_v(o['prompt']))} | {_cell(_v(o['value']))} | {o['source']} |")
            out += ["", "</details>", ""]
        notes = [n for n in it.get("notes", []) if not n.startswith("Traceback")]
        if notes:
            out += ["Notes:", ""] + [f"- {_cell(n)}" for n in notes] + [""]
    return "\n".join(out) + "\n"


def write(session: Path) -> Path:
    meta, _ = load(session)
    p = session / f"HG_report_{meta.get('mode', 'session')}.md"
    p.write_text(render(session), encoding="utf-8")
    return p


if __name__ == "__main__":
    print(write(Path(sys.argv[1])))

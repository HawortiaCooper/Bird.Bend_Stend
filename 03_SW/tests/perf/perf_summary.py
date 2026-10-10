"""Summary table of PR-1…PR-5 runs (``perf_gui.py`` results) against the SRS budgets.

Usage: ``python perf_summary.py <run dir> [<run dir> ...]`` — prints one Markdown table row per measured item
(value, budget, verdict, margin) plus the host load of each run.

Budgets (v0.5.5): NFR-001 / NFR-009 — refresh-tick interval p95 ≤ 50 ms and change → paint p95 ≤ 50 ms for every
changed pane (unchanged panes are not judged; the old worst-pane paint p95 is reported as informative); event-loop p99 ≤ 100 ms (reported); NFR-002 / NFR-003 p95
≤ 50 ms; SAF-SW-001 (TC-SAF-SW-001-01) p95 ≤ 50 ms; NFR-004 0 SW-attributable losses, growth ≤ 50 MB.

Verifies: NFR-001, NFR-002, NFR-003, NFR-004, NFR-009, SAF-SW-001 (report tool)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def verdict(value: float, budget: float) -> tuple[str, str]:
    if value != value:  # nan
        return "n/a", ""
    ok = value <= budget
    if budget == 0:
        return ("PASS" if ok else "FAIL"), ""
    return ("PASS" if ok else "FAIL"), f"{100.0 * (budget - value) / budget:+.0f} %"


def rows_for(run: Path) -> list[str]:
    r = json.loads((run / "results.json").read_text(encoding="utf-8"))
    out: list[str] = []
    name = run.name
    hl = r.get("hostload", {})
    load = (f"CPU p50 {hl.get('cpu_total_pct', {}).get('p50')} % / p95 {hl.get('cpu_total_pct', {}).get('p95')} %, "
            f"other python {hl.get('other_python_procs', {}).get('min')}–{hl.get('other_python_procs', {}).get('max')}"
            if hl else "n/a")

    def row(item: str, value: float, budget: float, extra: str = "") -> None:
        v, m = verdict(value, budget)
        out.append(f"| {name} | {item} | {value:.1f} | ≤ {budget:g} | {v} | {m} | {extra} | {load} |")

    if "pr1" in r:
        p = r["pr1"]
        mode = r["plots"]["mode"]
        rd = p.get("render") or r.get("render") or {}
        via = str(rd.get("via", "?"))
        req = "NFR-009" if mode == "all600" else "NFR-001"
        tag = (f"{req} ({mode}, {r['plots'].get('plot_windows')} windows, GPU {rd.get('gl', 'off')})")
        harness = " — harness-forced OpenGL: informative until the GUI offers it" if via.startswith("harness") else ""
        # v0.5.5 criterion (TC-NFR-001-01 / TC-NFR-009-01): content changes are pushed at >= 20 Hz (refresh tick) and
        # every changed pane repaints within 50 ms; unchanged panes (redraw skip, SW_design_GUI §4.8) are not judged
        row(f"{tag}: refresh-tick interval p95", p["tick_interval_ms"].get("p95", float("nan")), 50.0,
            f"max {p['tick_interval_ms'].get('max')} ms{harness}")
        if "change_to_paint_ms" in p:
            c = p["change_to_paint_ms"]
            wk = p.get("change_to_paint_worst_pane")
            wch = (p.get("panes_change", {}).get(wk) or {}).get("channels", []) if wk else []
            row(f"{tag}: change → paint p95, worst changed pane (>= 10 changes)",
                p.get("change_to_paint_worst_pane_p95_ms", float("nan")), 50.0,
                f"pane {wk} {wch}, unpainted {p.get('change_unpainted')}{harness}")
            row(f"{tag}: change → paint p95, all changes (informative)", c.get("p95", float("nan")), 50.0,
                f"n {c.get('n')}, max {c.get('max')} ms, changing panes {len(p.get('changing_panes', []))}")
            row(f"{tag}: paint p95 of continuously changing panes (informative)",
                p.get("changing_worst_pane_p95_ms", float("nan")), 50.0, "panes changing in >= 90 % of the ticks")
        row(f"{tag}: paint p95 worst pane incl. unchanged panes (informative, pre-v0.5.5 metric)",
            p.get("all_worst_pane_p95_ms", p["plot1_worst_pane_p95_ms"]), 50.0,
            f"min fps {p.get('all_min_fps', p['plot1_min_fps'])} (static lanes repaint only on change)")
        row("NFR-001 event-loop lateness p99", p["event_loop_late_ms"].get("p99", float("nan")), 100.0,
            f"max {p['event_loop_late_ms'].get('max')} ms")
    if "pr2" in r:
        p = r["pr2"]
        row("NFR-002 STOP click → wire p95", p["latency_ms"].get("p95", float("nan")), 50.0,
            f"n {p['latency_ms'].get('n')}, missing {p['missing']}, max {p['latency_ms'].get('max')} ms")
    if "pr3" in r:
        p = r["pr3"]
        row("NFR-003 Pause → HALT wire p95", p["latency_ms"].get("p95", float("nan")), 50.0,
            f"{p['path']}; n {p['latency_ms'].get('n')}, missing {p['missing']}")
    if "pr5" in r:
        p = r["pr5"]
        row("SAF-SW-001 trip → STOP p95 (PC)", p["pc_reader_to_stop_write_ms"].get("p95", float("nan")), 50.0,
            f"n {p['with_stop']}/{p['trials']}, wire p95 {p['wire_data_to_stop_ms'].get('p95')} ms")
    if "soak" in r:
        s = r["soak"]
        lk = s["link"]
        sw_loss = lk["async_overflow"] + s.get("rows_lost_final", 0) + s.get("wire_vs_csv", {}).get("missing_in_csv", 0)
        wv = s.get("wire_vs_csv", {})
        row("NFR-004 SW-attributable losses", float(sw_loss), 0.0,
            f"lost_link {lk['frames_lost_link']}, lost_fw {lk['frames_lost_fw']}, rows {s.get('csv_d_rows')}, "
            f"rows before record start {wv.get('rows_before_record_start')}, LINK LOST {len(s.get('link_lost_diag', []))}")
        row("NFR-004 memory growth after ref (MB)", float(s["growth_max_after_ref_mb"]), 50.0,
            f"WS {s['ws_mb_ref']} → {s['ws_mb_end']} MB; after min 20: {s.get('growth_after_20min_mb')} MB; "
            f"probes {'ON (harness lists grow)' if s.get('probes_installed', True) else 'off'}")
    return out


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print("| Run | Item | Value (ms / count / MB) | Budget | Verdict | Margin | Details | Host load |")
    print("|---|---|---|---|---|---|---|---|")
    for a in argv:
        for line in rows_for(Path(a)):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

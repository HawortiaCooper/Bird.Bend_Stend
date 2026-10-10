"""Summary table of PR-1…PR-5 runs (``perf_gui.py`` results) against the SRS budgets.

Usage: ``python perf_summary.py <run dir> [<run dir> ...]`` — prints one Markdown table row per measured item
(value, budget, verdict, margin) plus the host load of each run.

Budgets: NFR-001 paint-to-paint p95 ≤ 50 ms (≥ 20 fps), event-loop p99 ≤ 100 ms (reported); NFR-002 / NFR-003 p95
≤ 50 ms; SAF-SW-001 (TC-SAF-SW-001-01) p95 ≤ 50 ms; NFR-004 0 SW-attributable losses, growth ≤ 50 MB.

Verifies: NFR-001, NFR-002, NFR-003, NFR-004, SAF-SW-001 (report tool)
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
        row(f"NFR-001 paint p95 (worst Plot-1 pane, {r['plots']['mode']})", p["plot1_worst_pane_p95_ms"], 50.0,
            f"min fps {p['plot1_min_fps']}, tick p95 {p['tick_interval_ms'].get('p95')} ms")
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

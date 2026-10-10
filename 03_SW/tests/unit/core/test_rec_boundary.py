"""SWD-P3-01 (SW_perf_report_devpc, S3): rows received at a record-stop instant must never appear in the next
recording. The enqueue decision and the append are one lock hold with the state changes of start / stop; a row
received before the previous end instant but delivered after a new start is rejected (``rows_stale``).
(The Orchestrator asked for an SW-REC-* tag; the SRS has no SW-REC IDs — the recording requirements are SW-ACQ-002 /
SW-ACQ-004.)

Verifies: SW-ACQ-002, SW-ACQ-004
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from bend_stand.core import recorder as R
from bend_stand.core.clock import MONOTONIC, LockstepClock
from bend_stand.core.pipeline import DataRow


def _row(i: int, t_host: int) -> DataRow:
    return DataRow(t_host, i * 12_500, i * 12_500, i * 0.0125, i & 0xFFFF, 0, 4, 0, 100, 0, 0, 0, 0)


def _rows(folder: Path) -> list[int]:
    return [int(ln.split(",")[5]) for ln in (folder / "data.csv").read_text().splitlines() if ln.startswith("D,")]


@pytest.mark.req("SW-ACQ-002", "SW-ACQ-004")
def test_row_of_the_stop_instant_delivered_after_the_next_start_is_rejected(tmp_path) -> None:
    """Deterministic interleaving of the soak case: two rows received at the stop instant (t_host = stop time) are
    delivered by a lagging pipeline only after the next start."""
    clk = LockstepClock(start_ns=10**9)
    rec = R.Recorder(clk)
    rec.free_space_probe = lambda _p: 10 ** 12
    f1 = rec.start(tmp_path / "a", {})
    for i in range(3):
        clk.advance(ns=12_500_000)
        rec.on_row(_row(i, clk.monotonic_ns()))
    rec.step()
    t_stop = clk.monotonic_ns()
    rec.stop()
    clk.advance(ns=2 * 10**9)
    f2 = rec.start(tmp_path / "b", {})
    rec.on_row(_row(7621, t_stop))                        # received at the stop instant, delivered late
    rec.on_row(_row(7622, t_stop))
    clk.advance(ns=12_500_000)
    rec.on_row(_row(7783, clk.monotonic_ns()))            # the true first frame of recording 2
    rec.step()
    assert rec.rows_stale == 2
    rec.stop()
    assert _rows(f1) == [0, 1, 2] and _rows(f2) == [7783]


@pytest.mark.req("SW-ACQ-002", "SW-ACQ-004")
def test_threaded_stop_start_cycles_never_leak_rows(tmp_path) -> None:
    """A producer thread streams rows (receive stamp = now) while the main thread stops and starts recordings 30
    times: every recording contains only rows received after the previous recording ended, in order."""
    rec = R.Recorder(MONOTONIC)
    rec.free_space_probe = lambda _p: 10 ** 12
    stop = threading.Event()
    seq = [0]

    def produce() -> None:
        while not stop.is_set():
            seq[0] += 1
            rec.on_row(_row(seq[0] & 0xFFFF, MONOTONIC.monotonic_ns()))
            time.sleep(0.0002)

    folders: list[tuple[Path, int, int]] = []
    rec.start(tmp_path / "r0", {})
    th = threading.Thread(target=produce, daemon=True)
    th.start()
    try:
        prev_end = 0
        for k in range(30):
            time.sleep(0.01)
            f = rec.folder
            rec.stop()
            end = rec._end_host_ns  # noqa: SLF001
            folders.append((f, prev_end, end))
            prev_end = end
            rec.start(tmp_path / f"r{k + 1}", {})
    finally:
        stop.set()
        th.join(2.0)
        rec.stop()
    for f, lo, hi in folders:
        hosts = [int(ln.split(",")[4]) for ln in (f / "data.csv").read_text().splitlines() if ln.startswith("D,")]
        assert hosts == sorted(hosts)
        assert all(lo < h <= hi for h in hosts), (f.name, lo, hi, hosts[:3], hosts[-3:])

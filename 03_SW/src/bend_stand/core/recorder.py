"""Recorder skeleton (SW_design §8, §13.7/§13.8) — M1: ``data.csv`` + ``meta.json`` sidecar.

* Folder ``<root>/<YYYYMMDD_HHMMSS>_<specimen>_<number>/`` (sanitised).
* ``data.csv``: ``# `` header block (format id ``bird.bend.data/1``, versions, board UID, start time, pointer to the
  sidecar), then the column header; one ``D`` row per DATA frame exactly once and ``E`` rows for FW events,
  time-ordered by arrival (the pipeline delivers DATA and EVENT in arrival order).
* ``meta.json`` (``bird.bend.recording`` v1): written at start (marks, versions, dictionary hash, board
  parameters, thresholds) and rewritten at stop with link statistics and integrity
  ``{complete, rows_written, rows_lost, failures}``.
* Rows go through a bounded queue (60 s of rows); the writer runs in its own thread (real clock) or from
  ``step()`` (lockstep). A write error / disk full → state FAILED, ``rows_lost`` counted, never silent
  (SW-ACQ-004). Test hook ``fail(exc, after_rows)`` (hook g, ENOSPC).
M3 adds derived columns, marks edits, raw frame dump, free-space checks and take-sample rows.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/recorder.py @37c87471 (pattern: bounded queue + writer +
sidecar rewrite; rewritten for the bend columns, M1 subset).

Implements: SW-ACQ-002 (folder, CSV + sidecar), SW-ACQ-004 (integrity, no silent loss)
"""
from __future__ import annotations

import collections
import logging
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import Clock, wall_utc_iso
from bend_stand.core.errors import RecorderError
from bend_stand.core.model import FwEvent, RecordingStatus
from bend_stand.core.pipeline import DataRow
from bend_stand.core.schema import atomic_write_json

log = logging.getLogger("bend_stand.core.recorder")
COLUMNS = ("row_type", "t_dev_s", "t_us_u", "t_us", "t_host", "frame_seq", "seq_lost", "flags", "status", "valid",
           "moving", "raw", "raw_state", "setpoint_um", "x_mm", "event")
QUEUE_ROWS = 60 * 96
FORMAT_ID = "bird.bend.data/1"


def sanitise(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s.strip())
    return s.strip("-")[:40]


def folder_name(wall_iso: str, specimen: str = "", number: str = "") -> str:
    stamp = wall_iso[:19].replace("-", "").replace(":", "").replace("T", "_")
    parts = [stamp] + [p for p in (sanitise(specimen), sanitise(number)) if p]
    return "_".join(parts)


@dataclass
class _EventRow:
    t_host_ns: int
    ev: FwEvent


class Recorder:
    def __init__(self, clock: Clock, *, on_state: Callable[[RecordingStatus], None] | None = None) -> None:
        self.clock = clock
        self.on_state = on_state
        self._q: collections.deque[DataRow | _EventRow] = collections.deque()
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._f: Any = None
        self.folder: Path | None = None
        self.state = "IDLE"
        self.rows = 0
        self.rows_lost = 0
        self.failure: str | None = None
        self.meta: dict[str, Any] = {}
        self._fail_exc: BaseException | None = None
        self._fail_after = 0

    # ---- control ----------------------------------------------------------------------------------------
    def start(self, root: str | os.PathLike[str], meta: Mapping[str, Any], *, specimen: str = "",
              number: str = "") -> Path:
        if self.state == "RECORDING":
            raise RecorderError("already recording")
        wall = wall_utc_iso(self.clock)
        folder = Path(root) / folder_name(wall, specimen, number)
        folder.mkdir(parents=True, exist_ok=False)
        self.folder = folder
        self.rows = self.rows_lost = 0
        self.failure = None
        self.meta = {"schema": "bird.bend.recording", "schema_version": 1, "start_utc": wall,
                     "format": FORMAT_ID, "icd_version": pg.ICD_VERSION,
                     "param_dict_hash": f"0x{pgen.PARAM_DICT_HASH:08X}", **dict(meta),
                     "integrity": {"complete": False}}
        atomic_write_json(folder / "meta.json", self.meta)
        f = open(folder / "data.csv", "w", encoding="utf-8", newline="\n")  # noqa: SIM115 - closed in stop()
        f.write(f"# {FORMAT_ID}\n# start_utc {wall}\n# sidecar meta.json\n")
        for k in ("sw_version", "fw_version", "board_uid"):
            if k in meta:
                f.write(f"# {k} {meta[k]}\n")
        f.write(",".join(COLUMNS) + "\n")
        self._f = f
        self.state = "RECORDING"
        if not self.clock.is_lockstep:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="bend-recorder", daemon=True)
            self._thread.start()
        self._publish()
        return folder

    def stop(self, extra_meta: Mapping[str, Any] | None = None) -> Path | None:
        if self.state not in ("RECORDING", "FAILED"):
            return None
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(2.0)
        self.step()
        f, self._f = self._f, None
        if f is not None:
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except OSError as exc:  # pragma: no cover
                self._failed(exc)
        complete = self.failure is None and self.rows_lost == 0
        self.meta.update(dict(extra_meta or {}))
        self.meta["stop_utc"] = wall_utc_iso(self.clock)
        self.meta["integrity"] = {"complete": complete, "rows_written": self.rows, "rows_lost": self.rows_lost,
                                  "failures": [self.failure] if self.failure else []}
        folder = self.folder
        if folder is not None:
            try:
                atomic_write_json(folder / "meta.json", self.meta)
            except OSError as exc:  # pragma: no cover
                log.error("sidecar rewrite failed: %s", exc)
        self.state = "IDLE"
        self._publish()
        return folder

    def fail(self, exc: BaseException, after_rows: int = 0) -> None:
        """Test hook (g): raise ``exc`` from the next write after ``after_rows`` more rows."""
        self._fail_exc, self._fail_after = exc, self.rows + int(after_rows)

    # ---- sinks (pipeline thread) -------------------------------------------------------------------------
    def on_row(self, row: DataRow) -> None:
        self._enqueue(row)

    def on_event(self, ev: FwEvent) -> None:
        self._enqueue(_EventRow(ev.t_host_ns, ev))

    def _enqueue(self, item: DataRow | _EventRow) -> None:
        if self.state != "RECORDING":
            if self.state == "FAILED":
                self.rows_lost += 1
            return
        with self._cv:
            if len(self._q) >= QUEUE_ROWS:
                self.rows_lost += 1
                return
            self._q.append(item)
            self._cv.notify()

    # ---- writer ------------------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                if not self._q:
                    self._cv.wait(0.25)
            self.step()

    def step(self, now_ns: int | None = None) -> int:
        f = self._f
        n = 0
        while True:
            with self._cv:
                if not self._q:
                    break
                item = self._q.popleft()
            if f is None or self.state != "RECORDING":
                self.rows_lost += 1
                continue
            try:
                if self._fail_exc is not None and self.rows >= self._fail_after:
                    exc, self._fail_exc = self._fail_exc, None
                    raise exc
                f.write(self._format(item))
                self.rows += 1
                n += 1
            except OSError as exc:
                self.rows_lost += 1
                self._failed(exc)
        if n and f is not None and self.state == "RECORDING":
            try:
                f.flush()
            except OSError as exc:  # pragma: no cover
                self._failed(exc)
        return n

    def _failed(self, exc: BaseException) -> None:
        if self.state != "FAILED":
            self.state = "FAILED"
            self.failure = f"{type(exc).__name__}: {exc}"
            log.error("recording failed: %s", self.failure)
            self._publish()

    @staticmethod
    def _format(item: DataRow | _EventRow) -> str:
        if isinstance(item, _EventRow):
            e = item.ev
            arg = e.arg_name if e.arg_name is not None else str(e.arg)
            return (f"E,,,{e.t_us},{item.t_host_ns},,,,,,,,,,,"
                    f"{e.name}:{arg}:{e.value}:{e.value2}\n")
        r = item
        valid = r.flags & 1
        moving = (r.flags >> 1) & 1
        return (f"D,{r.t_dev_s:.6f},{r.t_us_u},{r.t_us},{r.t_host_ns},{r.frame_seq},{r.seq_lost},{r.flags},"
                f"{r.status},{valid},{moving},{r.raw},{r.raw_state},{r.setpoint_um},{r.setpoint_um / 1000.0:.6f},\n")

    def status(self) -> RecordingStatus:
        with self._cv:
            fill = 100.0 * len(self._q) / QUEUE_ROWS
        return RecordingStatus(self.state, str(self.folder) if self.folder else None, self.rows, self.rows_lost,  # type: ignore[arg-type]
                               fill, self.failure)

    def _publish(self) -> None:
        cb = self.on_state
        if cb is not None:
            cb(self.status())

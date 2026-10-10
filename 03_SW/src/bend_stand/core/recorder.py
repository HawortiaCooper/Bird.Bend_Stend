"""Recorder (SW_design §8, §13.7/§13.8; SRS SW-ACQ-002…004, SW-META-002, SW-LIM-003).

* Folder ``<root>/<YYYYMMDD_HHMMSS>_<specimen>_<number>/`` (sanitised, unique — ``_2`` … for a restart within the
  same second).
* ``data.csv``: ``# `` header block (format id ``bird.bend.data/1``, versions, board UID, start time, calibration,
  tare, limits, thresholds, x_zero, pointer to the sidecar), the column header, then one ``D`` row per DATA frame
  **exactly once** (raw fields, unwrapped time, flags, status, raw, setpoint, machine travel and every derived column
  of ``core.scaling.DERIVED_KEYS``) and interleaved ``E`` rows in arrival order: FW EVENTs (``NAME:arg:value:value2``)
  and SW events (``NAME:text`` — TARE, TARE_UNDO, VALID_ON/OFF, STOP / HALT / PAUSE / RESUME, SW_TRIP, NO_SPECIMEN_ON
  / OFF, MARK_EDIT, X_ZERO, CAL_*, TRAVEL_DIFF_*, REC_GAP …). SW event rows carry the receive stamp / device time of
  the newest DATA row, so the file stays ordered by arrival.
* ``meta.json`` (``bird.bend.recording`` v1): written at start (marks + automatic snapshot: board configuration,
  calibration record, tare, limits, verified thresholds, versions, dictionary hash, session, ``no_specimen_mode``,
  ``travel_cal_differs``) and rewritten at stop (``marks_final``, ``mark_edits``, link statistics, integrity
  ``{complete, rows_written, rows_lost, failures}``).
* ``samples.csv`` (take-sample rows, SW-ACQ-003) next to ``data.csv`` while recording, else
  ``<root>/samples/samples_<YYYYMMDD>.csv``.
* Back-pressure / failure (SW-ACQ-004): bounded queue (60 s of rows); ≥ 50 % → warning; a write / flush error, disk
  full or free space below ``max(500 MB, …)`` (checked at start and every 10 s) → state ``FAILED``, ``on_failure``
  callback (backend: topic ``rec.failure``, indicator, event); rows are never dropped silently (``rows_lost`` +
  ``REC_GAP`` row when writing resumes after a queue overflow; sidecar ``complete: false``). Test hook
  ``fail(exc, after_rows)`` (ENOSPC) and the free-space probe override.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/core/recorder.py @37c87471 (pattern: bounded queue + writer +
sidecar rewrite; rewritten for the bend columns).

Implements: SW-ACQ-002 (folder, CSV + sidecar, derived columns, event rows), SW-ACQ-003 (samples.csv), SW-ACQ-004
(integrity, failure handling, no silent loss), SW-META-002 (marks + snapshot), SW-LIM-003 (limits in the metadata)
"""
from __future__ import annotations

import collections
import logging
import math
import os
import re
import shutil
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bend_stand.core import params_gen as pgen
from bend_stand.core import protocol_gen as pg
from bend_stand.core.clock import Clock, wall_utc_iso
from bend_stand.core.errors import RecorderError
from bend_stand.core.model import FwEvent, RecordingStatus, SampleRow
from bend_stand.core.pipeline import DataRow
from bend_stand.core.scaling import DERIVED_KEYS
from bend_stand.core.schema import atomic_write_json

log = logging.getLogger("bend_stand.core.recorder")
COLUMNS = (("row_type", "t_dev_s", "t_us_u", "t_us", "t_host", "frame_seq", "seq_lost", "flags", "status", "valid",
            "moving", "raw", "raw_state", "setpoint_um", "x_mm") + DERIVED_KEYS + ("calc_reason", "op", "event"))
QUEUE_ROWS = 60 * 96
FORMAT_ID = "bird.bend.data/1"
FREE_MIN_BYTES = 500 * 1024 * 1024
FREE_CHECK_NS = 10_000_000_000
BYTES_PER_ROW = 300
SAMPLE_COLUMNS = ("utc", "t_dev_s", "window_s", "n", "f_mean_n", "f_std_n", "f_n", "x_mean_mm", "x_std_mm",
                  "raw_mean", "raw_std", "raw_n", "specimen", "number", "operator", "notes", "custom")
_EMPTY_DERIVED = "," * (len(DERIVED_KEYS) - 1)
_COL = {c: i for i, c in enumerate(COLUMNS)}
_E_TEMPLATE = ["E"] + [""] * (len(COLUMNS) - 1)


def sanitise(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s.strip())
    return s.strip("-")[:40]


def folder_name(wall_iso: str, specimen: str = "", number: str = "") -> str:
    stamp = wall_iso[:19].replace("-", "").replace(":", "").replace("T", "_")
    parts = [stamp] + [p for p in (sanitise(specimen), sanitise(number)) if p]
    return "_".join(parts)


def unique_folder(root: Path, name: str) -> Path:
    """Create ``root/name`` or, if it exists (stop + restart within the same second, SWD-M1-09), ``name_2``, …
    Raises ``RecorderError`` with a user-level text (never the raw OS message)."""
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecorderError(f"cannot create the recordings folder {root} ({_errname(exc)})") from exc
    for i in range(1, 1000):
        folder = root / (name if i == 1 else f"{name}_{i}")
        try:
            folder.mkdir(exist_ok=False)
            return folder
        except FileExistsError:
            continue
        except OSError as exc:
            raise RecorderError(f"cannot create the recording folder {folder.name} ({_errname(exc)})") from exc
    raise RecorderError(f"too many recordings named {name}")


def _errname(exc: OSError) -> str:
    import errno as _errno  # noqa: PLC0415

    return _errno.errorcode.get(exc.errno or 0, "OS error") if exc.errno else "OS error"


def _csv_text(s: str) -> str:
    """One CSV field without separators / line breaks (event texts)."""
    return re.sub(r"[,\r\n]+", ";", str(s))


#: first characters a spreadsheet treats as the start of a formula (CSV / formula injection, OWASP)
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(s: object) -> str:
    """One operator-text CSV field (marks in ``samples.csv``): separators / line breaks removed as in ``_csv_text``
    and a leading ``= + - @ TAB CR`` neutralised with a ``'`` prefix, so a spreadsheet shows the text instead of
    evaluating it (review finding SWR-23). Implements: SW-ACQ-003"""
    t = _csv_text(str(s))
    return "'" + t if t.startswith(_FORMULA_START) else t


def _g(v: float) -> str:
    return "nan" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.9g}"


def free_space(path: Path) -> int | None:
    try:
        p = path
        while not p.exists() and p.parent != p:
            p = p.parent
        return shutil.disk_usage(p).free
    except OSError:
        return None


def append_sample(path: Path, row: SampleRow) -> None:
    """Append one take-sample row (header when the file is new)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    m = row.marks
    custom = ";".join(f"{k}={v}" for k, v in m.custom)
    vals = (row.utc, f"{row.t_dev_s:.6f}", f"{row.window_s:g}", str(row.n), _g(row.f_mean), _g(row.f_std),
            str(row.f_n), _g(row.x_mean), _g(row.x_std), _g(row.raw_mean), _g(row.raw_std), str(row.raw_n),
            csv_cell(m.specimen), csv_cell(m.number), csv_cell(m.operator), csv_cell(m.notes), csv_cell(custom))
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(",".join(SAMPLE_COLUMNS) + "\n")
        f.write(",".join(vals) + "\n")


@dataclass
class _EventRow:
    t_host_ns: int
    ev: FwEvent


@dataclass
class _SwRow:
    t_host_ns: int
    t_us_u: int | None
    t_dev_s: float | None
    name: str
    text: str


class Recorder:
    def __init__(self, clock: Clock, *, on_state: Callable[[RecordingStatus], None] | None = None,
                 on_failure: Callable[[RecordingStatus], None] | None = None,
                 on_warning: Callable[[str], None] | None = None) -> None:
        self.clock = clock
        self.on_state = on_state
        self.on_failure = on_failure
        self.on_warning = on_warning
        self.free_space_probe: Callable[[Path], int | None] = free_space
        self.op_name: Callable[[], str] = lambda: ""
        self._q: collections.deque[DataRow | _EventRow | _SwRow] = collections.deque()
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
        self._last_host = 0
        self._last_t_u: int | None = None
        self._last_t_dev: float | None = None
        self._gap: list[int] | None = None          # [count, first t_us_u, last t_us_u] of a queue overflow
        self._warned_fill = False
        self._closing = False                       # SWR-05: the recording has ended (rows after it: not part of it)
        self.rows_after_end = 0
        self._end_host_ns: int | None = None        # SWD-P3-01: newest receive stamp of the previous recording
        self.rows_stale = 0                         # rows received before that end instant, delivered after a start
        self._next_free_check = 0
        self.mark_edits: list[dict[str, Any]] = []

    # ---- control ----------------------------------------------------------------------------------------
    def start(self, root: str | os.PathLike[str], meta: Mapping[str, Any], *, specimen: str = "",
              number: str = "", header: Mapping[str, Any] | None = None) -> Path:
        if self.state == "RECORDING":
            raise RecorderError("already recording")
        root_p = Path(root)
        free = self.free_space_probe(root_p)
        if free is not None and free < FREE_MIN_BYTES:
            raise RecorderError(f"not enough free disk space ({free // (1024 * 1024)} MB < "
                                f"{FREE_MIN_BYTES // (1024 * 1024)} MB)")
        wall = wall_utc_iso(self.clock)
        folder = unique_folder(root_p, folder_name(wall, specimen, number))
        self.folder = folder
        with self._cv:                              # SWR-05: nothing of a previous recording survives a start
            self._q.clear()
            self._gap = None
            self._warned_fill = False
            self._closing = False
            self.rows_stale = 0
            if self._end_host_ns is not None:
                self._last_host = max(self._last_host, self._end_host_ns)
        self.rows = self.rows_lost = self.rows_after_end = 0
        self.failure = None
        self.mark_edits = []
        self._next_free_check = self.clock.monotonic_ns() + FREE_CHECK_NS
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
        for k, v in (header or {}).items():
            f.write(f"# {k} {_csv_text(str(v)).replace(chr(10), ' ')}\n")
        f.write(",".join(COLUMNS) + "\n")
        self._f = f
        with self._cv:                              # SWD-P3-01: state changes under the enqueue lock
            self.state = "RECORDING"
        if not self.clock.is_lockstep:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="bend-recorder", daemon=True)
            self._thread.start()
        self._publish()
        return folder

    def stop(self, extra_meta: Mapping[str, Any] | None = None) -> Path | None:
        """End the recording. Rows received until the end instant (``_closing`` set, after the first sidecar write)
        are written — none is left in the queue for the next recording; rows received after it are not part of this
        recording (``rows_after_end``, informative). The sidecar is rewritten if the final drain changed the counts
        (SWR-05)."""
        # Implements: SW-ACQ-002, SW-ACQ-004 (every frame exactly once, never dropped silently; SWR-05)
        if self.state not in ("RECORDING", "FAILED"):
            return None
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(2.0)
        self.step()
        f = self._f
        if f is not None:
            try:
                f.flush()
            except OSError as exc:  # pragma: no cover
                self._failed(exc)
        self.meta.update(dict(extra_meta or {}))
        self.meta["mark_edits"] = list(self.mark_edits)
        self.meta["stop_utc"] = wall_utc_iso(self.clock)
        counts = self._write_sidecar()
        with self._cv:
            self._closing = True                     # the end instant: later rows are not part of this recording
            self._end_host_ns = self._last_host       # newest receive stamp that belongs to this recording
        self.step()                                  # rows received while the sidecar was written
        f, self._f = self._f, None
        if f is not None:
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except OSError as exc:  # pragma: no cover
                self._failed(exc)
        if self._counts() != counts:
            self._write_sidecar()
        with self._cv:
            self.state = "IDLE"
        self._publish()
        return self.folder

    def _counts(self) -> tuple[int, int, str | None]:
        return self.rows, self.rows_lost, self.failure

    def _write_sidecar(self) -> tuple[int, int, str | None]:
        counts = self._counts()
        self.meta["integrity"] = {"complete": self.failure is None and self.rows_lost == 0, "rows_written": self.rows,
                                  "rows_lost": self.rows_lost, "failures": [self.failure] if self.failure else []}
        folder = self.folder
        if folder is not None:
            try:
                atomic_write_json(folder / "meta.json", self.meta)
            except (OSError, ValueError) as exc:  # pragma: no cover
                log.error("sidecar rewrite failed: %s", exc)
        return counts

    def fail(self, exc: BaseException, after_rows: int = 0) -> None:
        """Test hook (g): raise ``exc`` from the next write after ``after_rows`` more rows."""
        self._fail_exc, self._fail_after = exc, self.rows + int(after_rows)

    def samples_path(self, root: str | os.PathLike[str]) -> Path:
        """Where a take-sample row goes now (SW-ACQ-003)."""
        if self.state == "RECORDING" and self.folder is not None:
            return self.folder / "samples.csv"
        stamp = wall_utc_iso(self.clock)[:10].replace("-", "")
        return Path(root) / "samples" / f"samples_{stamp}.csv"

    # ---- sinks (pipeline thread) -------------------------------------------------------------------------
    def on_row(self, row: DataRow) -> None:
        self._last_t_u, self._last_t_dev = row.t_us_u, row.t_dev_s
        self._enqueue(row, row.t_host_ns)

    def on_event(self, ev: FwEvent) -> None:
        self._enqueue(_EventRow(ev.t_host_ns, ev), ev.t_host_ns)

    def event_row(self, name: str, text: str = "") -> None:
        """SW event row (any thread); stamped with the newest row's receive time / device time (arrival order)."""
        self._enqueue(_SwRow(0, self._last_t_u, self._last_t_dev, name, text), None)

    def _enqueue(self, item: DataRow | _EventRow | _SwRow, t_host: int | None) -> None:
        """Pipeline / any thread. The decision (end instant, state, stale row) and the append happen under one lock
        hold together with the state changes of ``start`` / ``stop`` (SWD-P3-01): a row can no longer pass the check
        for one recording and land in the next. A row received before the previous recording's end instant but
        delivered after a new start is not part of the new recording (``rows_stale``)."""
        # Implements: SW-ACQ-002, SW-ACQ-004 (every frame exactly once, in its recording; SWD-P3-01)
        warn = False
        with self._cv:
            if self._closing:                       # after the end instant of a stopping recording (SWR-05)
                self.rows_after_end += 1
                return
            if self.state != "RECORDING":
                if self.state == "FAILED":
                    self.rows_lost += 1
                return
            if t_host is not None and self._end_host_ns is not None and int(t_host) <= self._end_host_ns:
                self.rows_stale += 1
                return
            if t_host is None:
                assert isinstance(item, _SwRow)
                item.t_host_ns = self._last_host
            else:
                self._last_host = max(self._last_host, int(t_host))
            if len(self._q) >= QUEUE_ROWS:
                self.rows_lost += 1
                t = getattr(item, "t_us_u", None) or 0
                if self._gap is None:
                    self._gap = [0, t, t]
                self._gap[0] += 1
                self._gap[2] = t
                return
            if self._gap is not None:
                n, a, b = self._gap
                self._gap = None
                self._q.append(_SwRow(self._last_host, b, None, "REC_GAP", f"{n} rows lost t_us_u {a}…{b}"))
            self._q.append(item)
            if not self._warned_fill and len(self._q) >= QUEUE_ROWS // 2:
                self._warned_fill = warn = True
            self._cv.notify()
        if warn and self.on_warning is not None:
            self.on_warning("recording queue ≥ 50 % full (disk too slow?)")

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
        now = self.clock.monotonic_ns() if now_ns is None else now_ns
        if self.state == "RECORDING" and now >= self._next_free_check and self.folder is not None:
            self._next_free_check = now + FREE_CHECK_NS
            free = self.free_space_probe(self.folder)
            if free is not None and free < FREE_MIN_BYTES:
                self._failed(OSError(28, f"free disk space below {FREE_MIN_BYTES // (1024 * 1024)} MB"))
        return n

    def _failed(self, exc: BaseException) -> None:
        if self.state != "FAILED":
            self.state = "FAILED"
            self.failure = f"{type(exc).__name__}: {exc}"
            log.error("recording failed: %s", self.failure)
            self._publish()
            cb = self.on_failure
            if cb is not None:
                try:
                    cb(self.status())
                except Exception:  # noqa: BLE001
                    log.exception("recording failure callback failed")

    def _format(self, item: DataRow | _EventRow | _SwRow) -> str:
        if isinstance(item, _EventRow):
            e = item.ev
            arg = e.arg_name if e.arg_name is not None else str(e.arg)
            out = _E_TEMPLATE.copy()
            out[_COL["t_us"]], out[_COL["t_host"]] = str(e.t_us), str(item.t_host_ns)
            out[_COL["event"]] = f"{e.name}:{arg}:{e.value}:{e.value2}"
            return ",".join(out) + "\n"
        if isinstance(item, _SwRow):
            out = _E_TEMPLATE.copy()
            out[_COL["t_dev_s"]] = "" if item.t_dev_s is None else f"{item.t_dev_s:.6f}"
            out[_COL["t_us_u"]] = "" if item.t_us_u is None else str(item.t_us_u)
            out[_COL["t_host"]] = str(item.t_host_ns)
            out[_COL["event"]] = f"{item.name}:{_csv_text(item.text)}"
            return ",".join(out) + "\n"
        r = item
        valid = r.flags & 1
        moving = (r.flags >> 1) & 1
        der = ",".join(_g(v) for v in r.derived) if r.derived else _EMPTY_DERIVED
        return (f"D,{r.t_dev_s:.6f},{r.t_us_u},{r.t_us},{r.t_host_ns},{r.frame_seq},{r.seq_lost},{r.flags},"
                f"{r.status},{valid},{moving},{r.raw},{r.raw_state},{r.setpoint_um},{r.setpoint_um / 1000.0:.6f},"
                f"{der},{r.calc_reason},{self.op_name()},\n")

    def status(self) -> RecordingStatus:
        with self._cv:
            fill = 100.0 * len(self._q) / QUEUE_ROWS
        return RecordingStatus(self.state, str(self.folder) if self.folder else None, self.rows, self.rows_lost,  # type: ignore[arg-type]
                               fill, self.failure)

    def _publish(self) -> None:
        cb = self.on_state
        if cb is not None:
            cb(self.status())


__all__ = ["Recorder", "COLUMNS", "append_sample", "free_space", "FORMAT_ID", "SAMPLE_COLUMNS"]

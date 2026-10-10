"""Application log file and incident log (SW_design §14, review finding SWR-14).

One shared set-up, called by the entry point (``bend_stand.__main__``) for GUI and headless runs — from the source
tree (``run.bat``) and from the frozen ``BirdBendStand.exe`` / ``BirdBendStand-cli.exe`` alike:

* **Rotating file** ``<data>\\logs\\bend_stand.log`` (``<data>`` = ``core.paths.app_data_dir``:
  ``BEND_STAND_DATA_DIR`` or ``%APPDATA%\\BirdBendStand``), 5 MiB × (1 + 10 backups), level INFO (DEBUG with
  ``--log-level DEBUG``). Records go through a ``QueueHandler``; one listener thread writes the file, so backend
  threads (Pipeline, Supervisor, …) never wait for the disk.
* **Console** handler on ``sys.stderr`` at the ``--log-level`` (default WARNING) — *not* installed when the frozen
  windowed exe already redirected stderr into its own log (runtime hook sets ``sys._bend_stand_stdio_log``), so no
  record is written twice (OI-F-RV-04).
* Uncaught exceptions of the main thread (``sys.excepthook``) and of any thread (``threading.excepthook``, chained
  before the liveness monitor's hook) and ``warnings`` are logged.
* **Incident log** (:func:`attach_incident_log`): link state changes, STOP / HALT / PAUSE issued and (not)
  confirmed, FW EVENTs (latches, limits, faults, clears), PC limit trips and clears, recording failures, sequence
  state changes and hotkey state are written at INFO or higher (WARNING / ERROR for losses, trips, unconfirmed
  stops, faults) — the post-incident evidence for the HW gate.

Idempotent: a second :func:`setup_logging` first removes the handlers and hooks of the previous one;
:func:`shutdown_logging` stops the listener and restores the hooks (also registered with ``atexit``).

Implements: SW-PLT-001 (application diagnostics), SAF-SW-005 (incident evidence), NFR-004 (bounded log size)
"""
from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import queue
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bend_stand.core import paths

LOG_FILE = "bend_stand.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 10
FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
#: attribute set on every handler this module installs (removal on re-setup, double-install check)
MARK = "_bend_stand_log"
#: attribute the frozen runtime hook sets on ``sys`` when it redirected stdout/stderr into its own log file
STDIO_REDIRECT_ATTR = "_bend_stand_stdio_log"

log = logging.getLogger("bend_stand.log")
incident_log = logging.getLogger("bend_stand.incident")


@dataclass
class LogSetup:
    path: Path | None                      # the rotating log file (None: file logging unavailable)
    console: bool                          # console handler installed
    level: int                             # console level
    file_level: int


_state: dict[str, Any] = {"listener": None, "sys_hook": None, "thread_hook": None, "prev_sys": None,
                          "prev_thread": None, "atexit": False, "setup": None, "prev_root_level": None}
_lock = threading.Lock()


def parse_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    v = logging.getLevelName(str(level).strip().upper())
    return v if isinstance(v, int) else logging.WARNING


def log_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    return paths.app_data_dir(data_dir) / "logs"


def our_handlers(logger: logging.Logger | None = None) -> list[logging.Handler]:
    return [h for h in (logger or logging.getLogger()).handlers if getattr(h, MARK, False)]


def setup_logging(level: str | int = "WARNING", *, data_dir: str | os.PathLike[str] | None = None,
                  file: bool = True, max_bytes: int = MAX_BYTES, backups: int = BACKUPS) -> LogSetup:
    """Install console + rotating-file logging for this process (replaces a previous set-up)."""
    with _lock:
        _shutdown_locked()
        root = logging.getLogger()
        _state["prev_root_level"] = root.level
        lvl = parse_level(level)
        fmt = logging.Formatter(FORMAT, DATEFMT)
        console = sys.stderr is not None and not getattr(sys, STDIO_REDIRECT_ATTR, None)
        if console:
            ch = logging.StreamHandler(sys.stderr)
            ch.setLevel(lvl)
            ch.setFormatter(fmt)
            setattr(ch, MARK, True)
            root.addHandler(ch)
        path: Path | None = None
        file_level = min(lvl, logging.INFO)
        if file:
            try:
                d = log_dir(data_dir)
                d.mkdir(parents=True, exist_ok=True)
                path = d / LOG_FILE
                fh = logging.handlers.RotatingFileHandler(path, maxBytes=int(max_bytes), backupCount=int(backups),
                                                          encoding="utf-8", delay=True)
                fh.setLevel(file_level)
                fh.setFormatter(fmt)
                q: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
                listener = logging.handlers.QueueListener(q, fh, respect_handler_level=True)
                listener.start()
                qh = logging.handlers.QueueHandler(q)
                qh.setLevel(file_level)
                setattr(qh, MARK, True)
                root.addHandler(qh)
                _state["listener"] = listener
            except Exception as exc:  # noqa: BLE001 — SWR-31: no data root / read-only profile: console only
                path = None
                log.warning("log file not available: %s", exc)
        root.setLevel(min(lvl, file_level) if path is not None else lvl)
        logging.captureWarnings(True)
        _install_hooks()
        if not _state["atexit"]:
            atexit.register(shutdown_logging)
            _state["atexit"] = True
        setup = LogSetup(path, console, lvl, file_level)
        _state["setup"] = setup
    log.info("log start: pid %d, argv %s, file %s", os.getpid(), sys.argv[1:], path)
    return setup


def _install_hooks() -> None:
    def sys_hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        prev = _state["prev_sys"]
        (prev or sys.__excepthook__)(exc_type, exc, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        if args.exc_type is not SystemExit:
            log.error("uncaught exception in thread %s", name,
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))  # type: ignore[arg-type]
        prev = _state["prev_thread"]
        (prev or threading.__excepthook__)(args)

    _state["prev_sys"], _state["prev_thread"] = sys.excepthook, threading.excepthook
    _state["sys_hook"], _state["thread_hook"] = sys_hook, thread_hook
    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def _shutdown_locked() -> None:
    root = logging.getLogger()
    for h in our_handlers(root):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
    listener = _state["listener"]
    _state["listener"] = None
    if listener is not None:
        try:
            listener.stop()                    # flushes the queue
        except Exception:  # noqa: BLE001
            pass
        for h in listener.handlers:
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
    if _state["sys_hook"] is not None and sys.excepthook is _state["sys_hook"]:
        sys.excepthook = _state["prev_sys"] or sys.__excepthook__
    if _state["thread_hook"] is not None and threading.excepthook is _state["thread_hook"]:
        threading.excepthook = _state["prev_thread"] or threading.__excepthook__
    _state["sys_hook"] = _state["thread_hook"] = None
    if _state["setup"] is not None:
        logging.captureWarnings(False)
        if _state["prev_root_level"] is not None:
            root.setLevel(_state["prev_root_level"])
    _state["setup"] = None


def shutdown_logging() -> None:
    """Remove this module's handlers, flush and stop the file writer, restore the exception hooks."""
    with _lock:
        _shutdown_locked()


def current() -> LogSetup | None:
    return _state["setup"]


# ============================================================================================ incident log
#: FW EVENT names logged at WARNING (latch set, limit, fault, link watchdog, AFE / NVM / clock problems, driver
#: alarm / power); the others (BOOT, MOVE_DONE, clears, HOMED, …) at INFO
ALARM_EVENTS = frozenset({"ESTOP_SET", "HALT_SET", "FAULT_SET", "LIMIT_SET", "LINK_WDG", "HOME_FAILED", "ALM_CHANGED",
                          "AFE_RATE_MISMATCH", "AFE_STALE", "AFE_REINIT", "NVM_ERROR", "CLK_FALLBACK", "DRIVER_POWER",
                          "NOT_SETTLED"})


class IncidentLogger:
    """EventBus observer: one log line per incident (strong subscription; returns in microseconds — the file is
    written by the listener thread)."""

    TOPICS = ("link.state", "link.compat", "stop.issued", "stop.confirmed", "stop.unconfirmed", "fw.event",
              "safety.trip", "safety.trip_cleared", "rec.failure", "seq.status", "hotkey.state", "resume.ignored",
              "safety.thresholds", "log")
    #: ``log`` records that are incidents (SWR-36): lost FW EVENTs
    LOG_KINDS = frozenset({"FW_EVENTS_LOST"})

    def __init__(self) -> None:
        self._seq_state: str | None = None
        self._thr_state: str | None = None
        self.tokens: list[int] = []

    def attach(self, events: Any) -> IncidentLogger:
        for topic in self.TOPICS:
            self.tokens.append(events.subscribe(topic, self.on_event, weak=False))
        return self

    def detach(self, events: Any) -> None:
        for t in self.tokens:
            events.unsubscribe(t)
        self.tokens.clear()

    def on_event(self, rec: Any) -> None:
        topic, p = rec.topic, rec.payload
        lvl, text = logging.INFO, None
        if topic == "link.state":
            state = str(getattr(getattr(p, "state", None), "value", getattr(p, "state", "?")))
            lvl = logging.WARNING if state in ("LOST", "LINK_LOST", "DEGRADED", "INCOMPATIBLE") else logging.INFO
            text = f"link {state} {getattr(p, 'endpoint', '') or ''} ({getattr(p, 'why', '')})"
        elif topic == "stop.issued":
            sent = bool(getattr(p, "sent", False))
            lvl = logging.INFO if sent else logging.ERROR
            text = (f"{getattr(p, 'cmd', '?')} issued by {getattr(p, 'source', '?')}: "
                    f"{'written' if sent else 'NOT WRITTEN'} {getattr(p, 'error', None) or ''} "
                    f"{getattr(p, 'reason', '')}").rstrip()
        elif topic in ("stop.confirmed", "stop.unconfirmed"):
            ok = topic == "stop.confirmed"
            lvl = logging.INFO if ok else logging.ERROR
            text = (f"{getattr(p, 'cmd', '?')} ({getattr(p, 'source', '?')}) "
                    f"{'confirmed' if ok else 'NOT CONFIRMED'} after {getattr(p, 'attempts', '?')} frame(s)")
        elif topic == "fw.event":
            name = str(getattr(p, "name", "?"))
            lvl = logging.WARNING if name in ALARM_EVENTS else logging.INFO
            arg = getattr(p, "arg_name", None) or getattr(p, "arg", "")
            text = (f"FW EVENT {name} arg {arg} value {getattr(p, 'value', '')}/{getattr(p, 'value2', '')} "
                    f"seq {getattr(p, 'seq', '')} t_us {getattr(p, 't_us', '')}")
        elif topic == "safety.trip":
            lvl = logging.WARNING
            text = (f"PC limit trip {getattr(p, 'limit', '?')}: value {getattr(p, 'value', '')} "
                    f"threshold {getattr(p, 'threshold', '')} {getattr(p, 'unit', '')} {getattr(p, 'text', '')}")
        elif topic == "safety.trip_cleared":
            text = f"PC limit trip cleared: {getattr(p, 'limit', p)}"
        elif topic == "rec.failure":
            lvl = logging.ERROR
            text = f"recording failed: {getattr(p, 'failure', p)} ({getattr(p, 'folder', '')})"
        elif topic == "safety.thresholds":                # SWR-36: FW-threshold verification FAILED / INVALID
            state = str(getattr(p, "state", "?"))
            if state == self._thr_state:
                return
            self._thr_state = state
            lvl = logging.ERROR if state in ("FAILED", "INVALID") else logging.INFO
            text = f"FW load thresholds {state} {getattr(p, 'text', '') or ''}".rstrip()
        elif topic == "log":                             # SWR-36: only the incident kinds of the SW event log
            if not isinstance(p, dict) or p.get("kind") not in self.LOG_KINDS:
                return
            lvl, text = logging.WARNING, str(p.get("text", ""))
        elif topic == "seq.status":
            state = str(getattr(p, "state", "?"))
            if state == self._seq_state:
                return                          # only state changes, not every phase / progress update
            self._seq_state = state
            lvl = logging.WARNING if state in ("ABORTED", "ERROR") else logging.INFO
            text = f"sequence {state} {getattr(p, 'end_reason', None) or ''}".rstrip()
        else:
            text = f"{topic}: {p!r}"[:400]
        incident_log.log(lvl, "%s", text)


def attach_incident_log(events: Any) -> IncidentLogger:
    """Subscribe an :class:`IncidentLogger` to a backend ``EventBus`` (``backend.events``)."""
    return IncidentLogger().attach(events)


__all__ = ["LOG_FILE", "MAX_BYTES", "BACKUPS", "MARK", "STDIO_REDIRECT_ATTR", "LogSetup", "setup_logging",
           "shutdown_logging", "current", "log_dir", "our_handlers", "parse_level", "IncidentLogger",
           "attach_incident_log"]

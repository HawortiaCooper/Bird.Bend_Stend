"""``QtBridge``: backend EventBus topics and futures → queued Qt signals in the GUI thread (SW_design_GUI §9.2).

* Every topic of ``core.api.TOPICS`` (B §15.3) is mapped to one signal (:data:`TOPIC_SIGNALS`) or listed in
  :data:`IGNORED_TOPICS` with a reason (G-01b). Backend callbacks run on backend threads: they only ``emit`` the
  frozen ``EventRecord`` (T2); Qt delivers it queued to GUI-thread receivers. All records are also emitted on
  ``anyEvent`` (Event log).
* Subscriptions use bound methods of this long-lived object (held weakly by the backend, B §15.4 rule 3);
  :meth:`shutdown` unsubscribes every token.
* Futures (``*_async``): :meth:`watch` attaches a done-callback that holds only the immortal relay and an integer
  token (no widget reference in a worker thread, SWD-PM3-07); ``on_ok`` / ``on_err`` run in the GUI thread. The
  GUI never calls ``Future.result()`` on a pending future (T3).
* High-rate data is never a signal: the refresh timer pulls ``status()`` / ``data.*`` (B §15.4 rule 4).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/bridge.py @37c87471 (adapted: topic → signal table over the
bend-stand EventBus, no polling timers, ``watch`` instead of ``call_async``; relay pattern unchanged).

Implements: SW-PLT-002 (GUI/backend separation over queued signals), NFR-004 (GUI part: T1–T3, T8)
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import weakref
from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal, Slot
from shiboken6 import Shiboken

from bend_stand.core import api as _api
from bend_stand.core.api import TOPICS

#: topics B publishes before they are listed in ``api.TOPICS`` (B6-33: listed there once the GUI maps them)
TOPICS_PENDING_GUI: tuple[str, ...] = tuple(getattr(_api, "TOPICS_PENDING_GUI", ()))
#: every topic the bridge knows (``TOPICS`` + pending ones)
KNOWN_TOPICS: tuple[str, ...] = tuple(dict.fromkeys((*TOPICS, *TOPICS_PENDING_GUI)))

log = logging.getLogger(__name__)

#: topic → signal name (SW_design_GUI §9.2)
TOPIC_SIGNALS: Mapping[str, str] = {
    "link.state": "linkChanged", "link.compat": "linkChanged", "device.info": "linkChanged",
    "device.params": "paramsChanged",
    "device.status": "deviceStatus",
    "fw.event": "fwEvent",
    "stop.issued": "stopIssued", "stop.confirmed": "stopIssued", "stop.unconfirmed": "stopIssued",
    "indicators.changed": "indicatorsChanged",
    "safety.trip": "safetyEvent", "safety.warning": "safetyEvent", "safety.thresholds": "safetyEvent",
    "safety.no_specimen": "safetyEvent",
    "motion.done": "motionEvent", "motion.target": "motionEvent", "motion.dropped": "motionEvent",
    "tare.state": "engineState", "cal.travel.state": "engineState", "cal.load.state": "engineState",
    "cal.travel.restore": "travelRestore",
    "channels.changed": "channelsChanged",
    "seq.status": "sequenceEvent", "seq.step_result": "sequenceEvent", "seq.window": "sequenceEvent",
    "rec.state": "recordingEvent", "rec.failure": "recordingEvent", "sample.taken": "recordingEvent",
    "marks.edited": "recordingEvent",
    "resume.ignored": "resumeIgnored",
    "hotkey.state": "hotkeyEvent", "hotkey.test": "hotkeyEvent",
    "report.ready": "reportReady",
    "log": "logMessage",
    "device.board_changed": "boardChanged",      # B6-33 (6), SWR-19: another board (UID) after a reconnect
}

if "safety.trip_cleared" in TOPICS:          # B6-15 (MC3-5): trip clear on its own topic
    TOPIC_SIGNALS = {**TOPIC_SIGNALS, "safety.trip_cleared": "safetyEvent"}

#: topics deliberately not subscribed (with the reason) — none at present (G-01b)
IGNORED_TOPICS: Mapping[str, str] = {}


class _DoneRelay(QObject):
    """Process-wide relay for Future completions (SWD-PM3-07). A Future's done-callback runs in the completing
    thread and holds only this immortal relay (created in the GUI thread) and an integer bridge token; the queued
    ``done`` signal is dispatched in the GUI thread to the bridge that is still alive."""

    done = Signal(int, object)

    def __init__(self) -> None:
        super().__init__()
        self._bridges: weakref.WeakValueDictionary[int, QtBridge] = weakref.WeakValueDictionary()
        self._next = 1
        self.done.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def register(self, bridge: "QtBridge") -> int:
        token, self._next = self._next, self._next + 1
        self._bridges[token] = bridge
        return token

    def unregister(self, token: int) -> None:
        self._bridges.pop(token, None)

    def callback(self, token: int) -> Callable[[cf.Future], None]:
        emit = self.done.emit
        return lambda fut: emit(token, fut)

    @Slot(int, object)
    def _dispatch(self, token: int, fut: cf.Future) -> None:
        bridge = self._bridges.get(token)
        if bridge is None:
            return
        if not Shiboken.isValid(bridge):
            self._bridges.pop(token, None)
            return
        bridge._on_done(fut)


_RELAY: _DoneRelay | None = None


def _relay() -> _DoneRelay:
    global _RELAY
    if _RELAY is None:
        _RELAY = _DoneRelay()
    return _RELAY


class QtBridge(QObject):
    """Signals carry the backend's ``EventRecord`` (``topic``, ``t_host_ns``, frozen ``payload``)."""

    linkChanged = Signal(object)
    paramsChanged = Signal(object)
    deviceStatus = Signal(object)
    fwEvent = Signal(object)
    stopIssued = Signal(object)
    indicatorsChanged = Signal(object)
    safetyEvent = Signal(object)
    motionEvent = Signal(object)
    engineState = Signal(object)
    travelRestore = Signal(object)
    channelsChanged = Signal(object)
    sequenceEvent = Signal(object)
    recordingEvent = Signal(object)
    resumeIgnored = Signal(object)
    hotkeyEvent = Signal(object)
    reportReady = Signal(object)
    logMessage = Signal(object)
    boardChanged = Signal(object)
    anyEvent = Signal(object)
    busyChanged = Signal(bool)

    def __init__(self, backend: Any, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.backend = backend
        self._pending: dict[cf.Future, tuple[Any, Any, str]] = {}
        self._token = _relay().register(self)
        self._tokens: list[Any] = []
        self._closed = False
        events = getattr(backend, "events", None)
        if events is not None:
            for topic in KNOWN_TOPICS:
                if topic in IGNORED_TOPICS or topic not in TOPIC_SIGNALS:
                    continue
                try:
                    self._tokens.append(events.subscribe(topic, self._on_event, weak=True))
                except Exception:  # noqa: BLE001 - a backend without a topic must not break the GUI
                    log.warning("subscribe(%s) failed", topic, exc_info=True)

    # ---------------------------------------------------------------- backend-thread callback (T2)
    def _on_event(self, record: Any) -> None:
        """Runs on a backend thread: only emits (queued delivery to GUI-thread receivers)."""
        name = TOPIC_SIGNALS.get(getattr(record, "topic", ""))
        if name is None or self._closed:
            return
        getattr(self, name).emit(record)
        self.anyEvent.emit(record)

    # ---------------------------------------------------------------- futures
    def watch(self, fut: cf.Future | None, on_ok: Callable[[Any], None] | None = None,
              on_err: Callable[[BaseException], None] | None = None, label: str = "call") -> cf.Future | None:
        """Track ``fut``; ``on_ok(result)`` / ``on_err(exc)`` run later in the GUI thread (never synchronously)."""
        if fut is None:
            return None
        self._pending[fut] = (on_ok, on_err, label)
        if len(self._pending) == 1:
            self.busyChanged.emit(True)
        fut.add_done_callback(_relay().callback(self._token))
        return fut

    def _on_done(self, fut: cf.Future) -> None:
        entry = self._pending.pop(fut, None)
        if entry is None:
            return
        on_ok, on_err, label = entry
        if not self._pending:
            self.busyChanged.emit(False)
        try:
            exc = fut.exception()
        except cf.CancelledError as e:
            exc = e
        try:
            if exc is not None:
                if on_err is not None:
                    on_err(exc)
                else:
                    log.error("%s failed: %s", label, exc)
                return
            if on_ok is not None:
                on_ok(fut.result())
        except Exception:  # noqa: BLE001 - T9: an exception never propagates into Qt
            log.exception("completion handler of %s failed", label)

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def shutdown(self) -> None:
        """Unsubscribe every topic and unregister from the relay (late completions are dropped)."""
        self._closed = True
        _relay().unregister(self._token)
        self._pending.clear()
        events = getattr(self.backend, "events", None)
        for tok in self._tokens:
            try:
                events.unsubscribe(tok)
            except Exception:  # noqa: BLE001
                pass
        self._tokens.clear()

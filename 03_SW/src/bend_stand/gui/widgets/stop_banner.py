"""``StopBanner``: full-width banner below the toolbar for latches, faults and stop results
(SW_design_GUI §2.3). Shows the most severe row with "+n more"; hidden when nothing applies.

Severity order: STOP/HALT/PAUSE not sent or unconfirmed > ESTOP > K1_WELDED > driver power lost > HALT >
faults > SW trip > LINK LOST > PAUSED > recording failure > first use > sent.

All clear texts are the backend's ``clear_hint`` (B §6.5); the GUI holds no stop logic. D-36: the only physical
stop is the red E-stop button (no holding STOP/BREAK button), so "not sent" texts point there.

Implements: SW-STOP-001 (real ``StopResult`` shown, never "sent" when it was not), SW-STOP-003 (latch + clear
procedure), SAF-SW-005 (clear procedure per latch), SW-STOP-004 (PAUSED + "Clear stop first")
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from bend_stand.core import protocol_gen as pg
from bend_stand.core.api import GateId
from bend_stand.gui import gating
from bend_stand.gui.indicator_map import source_text
from bend_stand.gui.theme import BANNER_STYLE

SENT_AUTOHIDE_S = 10.0
E_STOP_HINT = "use the red E-stop button"


@dataclass(frozen=True)
class BannerRow:
    rank: int                 # 0 = most severe
    style: str                # alarm / warn / info
    text: str
    action: str = ""          # "" | clear | resume | connection
    key: str = ""


@dataclass
class RecentStop:
    cmd: str
    source: str
    sent: bool
    reason: str
    t_mono: float
    wall: str
    state: str                # sent / not_sent / confirmed / unconfirmed


def stop_event_cmd(payload: Any) -> str:
    """Command named by a ``stop.issued`` / ``stop.confirmed`` / ``stop.unconfirmed`` payload (SWD-M1-06): an object
    with ``.cmd`` (StopResult / B's confirmation dataclass) or, from the M1 backend, the bare command name (str)."""
    if isinstance(payload, str):
        return payload.upper() or "STOP"
    cmd = getattr(payload, "cmd", None) or getattr(payload, "kind", None) or getattr(payload, "name", None)
    if cmd is None:
        return "STOP"
    return str(getattr(cmd, "name", cmd)).upper()


def _hint(item: Any) -> str:
    h = getattr(item, "clear_hint", None)
    return f" {h}" if h else ""


def with_hint(text: str, item: Any) -> str:
    """Gate-item text and its ``clear_hint`` with a visible separator (OI-UM-05 a): "<text> – <hint>". The text stays
    verbatim (a trailing period is dropped only to avoid ". –"); no hint → the text alone."""
    h = str(getattr(item, "clear_hint", None) or "").strip()
    t = str(text).rstrip()
    if not h:
        return t
    return f"{t.rstrip('.').rstrip()} – {h}"


def _on(ind: Any, name: str) -> Any:
    try:
        it = ind[name]
    except (KeyError, TypeError):
        return None
    return it if getattr(it, "state", None) == "ON" else None


LINK_LOST_STOP_WINDOW_S = 5.0


def link_lost_text(recent: RecentStop | None, now: float) -> str:
    """SWD-M1-10: "STOP sent" only when a STOP result with ``sent=True`` was reported shortly before (by the GUI or
    the backend's ``stop.issued``); otherwise only "LINK LOST"."""
    if recent is not None and recent.cmd == "STOP" and recent.sent and now - recent.t_mono < LINK_LOST_STOP_WINDOW_S:
        return "LINK LOST – STOP sent; sequence terminated. Reconnecting…"
    return "LINK LOST – reconnecting…"


def banner_rows(status: Any, recent: RecentStop | None, now: float) -> list[BannerRow]:
    """Pure: rows for one status snapshot + the most recent stop result (sorted by severity)."""
    rows: list[BannerRow] = []
    ind = getattr(status, "indicators", None)
    if recent is not None:
        if recent.state == "not_sent":
            rows.append(BannerRow(0, "alarm", f"{recent.cmd} NOT SENT ({recent.source}, {recent.wall}): "
                                              f"{recent.reason or 'not connected'} – {E_STOP_HINT}.", key="recent"))
        elif recent.state == "unconfirmed":
            rows.append(BannerRow(0, "alarm", f"{recent.cmd} NOT CONFIRMED by the board after 1 s – "
                                              f"{E_STOP_HINT}.", key="recent"))
    if ind is not None:
        if (it := _on(ind, "estop")) is not None:
            rows.append(BannerRow(1, "alarm", "E-STOP active – pulses stopped, driver disabled, axis NOT homed (load "
                                              "may back-drive)." + _hint(it),
                                  "clear", "estop"))
        if (it := _on(ind, "k1_welded")) is not None:
            rows.append(BannerRow(2, "alarm", f"K1_WELDED: {pg.FAULTS_DESC.get('K1_WELDED', '')}." + _hint(it),
                                  "clear", "k1_welded"))
        drv = ind.get("drv_pwr") if hasattr(ind, "get") else None
        if drv is not None and getattr(drv, "state", None) == "OFF":
            rows.append(BannerRow(3, "alarm", "Driver power lost – motion refused, axis NOT homed." + _hint(drv),
                                  "", "drv_pwr"))
        if (it := _on(ind, "halt")) is not None:
            src = f" – source: {source_text(it)}" if source_text(it) else ""
            rows.append(BannerRow(4, "alarm", f"HALT latched{src}. Motion refused." + _hint(it), "clear", "halt"))
        for name in pg.FAULTS_BITS:
            if not name or name == "K1_WELDED":
                continue
            if (it := _on(ind, name.lower())) is not None:
                val = f" ({it.value:g})" if getattr(it, "value", None) is not None else ""
                rows.append(BannerRow(5, "alarm", f"{name}: {pg.FAULTS_DESC.get(name, '')}{val}." + _hint(it),
                                      "clear", name.lower()))
    safety = getattr(status, "safety", None)
    trip = getattr(safety, "sw_trip", None)
    if trip:
        detail = getattr(getattr(safety, "trip", None), "text", "") or str(trip)     # B5-04 / B5-05 SwTrip
        rows.append(BannerRow(6, "alarm", f"SW limit trip: {detail} – STOP sent, sequence terminated. Move back / "
                                          "unload – the latch clears inside the limit.", "", "sw_trip"))
    link = getattr(status, "link", None)
    if str(getattr(getattr(link, "state", None), "value", "")) == "LOST":
        rows.append(BannerRow(7, "warn", link_lost_text(recent, now), "connection", "link"))
    if ind is not None and (it := _on(ind, "paused")) is not None:
        src = f" ({source_text(it)})" if source_text(it) else ""
        text = f"PAUSED{src} – motion blocked."
        first = gating.clear_first_items(gating.gate_of(status, GateId.RESUME))
        if first:
            text += " Clear stop first: " + "; ".join(i.text for i in first) + "."
            rows.append(BannerRow(8, "warn", text, "clear", "paused"))
        else:
            rows.append(BannerRow(8, "warn", text, "resume", "paused"))
    rec = getattr(status, "recording", None)
    if getattr(rec, "state", "") == "FAILED":
        rows.append(BannerRow(9, "alarm", f"RECORDING FAILED: {getattr(rec, 'failure', '')} – sequence stopped "
                                          "(controlled).", "", "rec"))
    move = gating.gate_of(status, GateId.MOVE)
    if move is not None:
        for item in move.refused:                       # B6-33 (1), SWR-22: board values outside the dictionary
            if item.code == "PARAM_INVALID":
                rows.append(BannerRow(10, "alarm", "Motion refused: " + with_hint(item.text, item), "connection",
                                      "param_invalid"))
                break
        for item in move.refused:
            if item.code == "LOAD_INPUT_INVALID":
                action = "" if getattr(getattr(status, "safety", None), "no_specimen_mode", False) else "nospec"
                rows.append(BannerRow(11, "warn", with_hint(item.text, item), action, "first_use"))  # SAF-SW-001
                break
    if recent is not None and recent.state in ("sent", "confirmed") and now - recent.t_mono < SENT_AUTOHIDE_S:
        if recent.cmd == "PAUSE":
            text = f"PAUSE sent ({recent.source}, {recent.wall}) – controlled stop, motion blocked until Resume."
        else:
            text = f"{recent.cmd} sent ({recent.source}, {recent.wall}) – axis stopped, driver holding."
        if recent.state == "confirmed":
            text += " Confirmed by the board."
        rows.append(BannerRow(12, "info", text, "", "recent"))
    rows.sort(key=lambda r: r.rank)
    return rows


class StopBanner(QFrame):
    clearStopRequested = Signal()
    resumeRequested = Signal()
    connectionRequested = Signal()
    noSpecimenRequested = Signal()

    def __init__(self, parent: QWidget | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self.setObjectName("stopBanner")
        self._clock = clock
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        self.label = QLabel("", self)
        self.label.setWordWrap(True)
        f = self.label.font()
        f.setBold(True)
        self.label.setFont(f)
        lay.addWidget(self.label, 1)
        self.action_button = QPushButton("", self)
        self.more_button = QPushButton("", self)
        for b in (self.action_button, self.more_button):
            b.setAutoDefault(False)
            lay.addWidget(b)
        self.action_button.clicked.connect(self._on_action)
        self.more_button.clicked.connect(self.clearStopRequested)
        self.recent: RecentStop | None = None
        self.rows: list[BannerRow] = []
        self._action = ""
        self._style = ""
        self.hide()

    # ---------------------------------------------------------------- inputs
    def show_stop_result(self, result: Any) -> None:
        """A ``StopResult`` from a STOP/HALT/PAUSE call or the ``stop.issued`` topic."""
        sent = bool(getattr(result, "sent", False))
        reason = getattr(result, "reason", "") or getattr(result, "error", "") or ""
        self.recent = RecentStop(cmd=stop_event_cmd(result), source=str(getattr(result, "source", "")),
                                 sent=sent, reason=str(reason), t_mono=self._clock(),
                                 wall=time.strftime("%H:%M:%S"), state="sent" if sent else "not_sent")

    def on_stop_event(self, record: Any) -> None:
        topic = getattr(record, "topic", "")
        payload = getattr(record, "payload", None)
        if topic == "stop.issued":
            if payload is not None:
                self.show_stop_result(payload)
            return
        cmd = stop_event_cmd(payload)
        if self.recent is None or self.recent.cmd != cmd:
            src = "" if isinstance(payload, str) else str(getattr(payload, "source", "") or "")
            self.recent = RecentStop(cmd, src, True, "", self._clock(), time.strftime("%H:%M:%S"), "sent")
        if topic == "stop.confirmed" and self.recent.state == "sent":
            self.recent.state = "confirmed"
        elif topic == "stop.unconfirmed":
            self.recent.state = "unconfirmed"

    def update_status(self, status: Any) -> None:
        self.rows = banner_rows(status, self.recent, self._clock())
        if not self.rows:
            if not self.isHidden():
                self.hide()
            return
        top = self.rows[0]
        text = top.text
        if self.label.text() != text:
            self.label.setText(text)
        style = BANNER_STYLE.get(top.style, BANNER_STYLE["info"])
        if style != self._style:
            self._style = style
            self.setStyleSheet(f"QFrame#stopBanner {{{style}}}")
        self._action = top.action
        label = {"clear": "Clear stop…", "resume": "Resume", "connection": "Connection tab",
                 "nospec": "Enter no-specimen mode…"}.get(top.action, "")
        self.action_button.setText(label)
        self.action_button.setVisible(bool(label))
        more = len(self.rows) - 1
        self.more_button.setText(f"+{more} more")
        self.more_button.setVisible(more > 0)
        if self.isHidden():
            self.show()

    def top_text(self) -> str:
        return self.rows[0].text if self.rows else ""

    def _on_action(self) -> None:
        sig = {"clear": self.clearStopRequested, "resume": self.resumeRequested,
               "connection": self.connectionRequested, "nospec": self.noSpecimenRequested}.get(self._action)
        if sig is not None:
            sig.emit()

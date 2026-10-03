"""Connection & Config tab (SW_design_GUI §3.1).

Connection: endpoint selector (``backend.endpoints()``; no port is opened until the operator clicks Connect,
D-06), Connect / Disconnect (futures via the bridge), device line (``DeviceInfo`` incl. generated feature names),
version check (``Compat``), link-statistics line (1 Hz) + [Details…].

Board configuration: :class:`ParamForm` generated from ``config.metas()`` / ``groups()`` with locked session rows
(``config.locked_keys()``); live rule check ``config.check(edits)`` debounced 200 ms (H1…H5, B31-04) — an ERROR
disables [Write & verify]; Write & verify → per-row statuses; Read all / Revert edits; Save / Load file
(``*.bbboard.json``, Edit column only + report); Save to NVM / Reload from NVM / Restore defaults (C-04);
Save & reboot (C-11, enabled while ``status().reboot_pending``); CFG state line.

All writes are gated by ``status().gates['config_write']`` (REFUSE → disabled with the texts as tooltip) and by
``config_read_only`` (IF-008). The tab holds no rule logic (P1).

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/tabs/connection_tab.py @37c87471 (structure only: endpoint
row, device/link lines, form + button rows; rewritten for the bend-stand ``core.api``).

Implements: SW-PLT-003, IF-008, SW-CFG-001, SW-CFG-002, SW-CFG-003, SW-CFG-004, FW-CFG-004 (device line)
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bend_stand.core.api import Compat, FileFormatError, GateId
from bend_stand.gui import gating
from bend_stand.gui.dialogs.confirm_dialog import make_confirm
from bend_stand.gui.dialogs.file_report import FileReportDialog
from bend_stand.gui.dialogs.link_stats import LinkStatsDialog
from bend_stand.gui.dialogs.safe_dialog import get_open_file_name, get_save_file_name
from bend_stand.gui.format import fmt_value, fmt_version
from bend_stand.gui.widgets.endpoint_selector import EndpointSelector
from bend_stand.gui.widgets.param_form import ParamForm

log = logging.getLogger(__name__)

RULE_DEBOUNCE_MS = 200
FILE_FILTER = "Board configuration (*.bbboard.json);;All files (*)"


def _err_text(exc: BaseException) -> str:
    return str(getattr(exc, "user_text", "") or exc or type(exc).__name__)


def device_line(link: Any) -> str:
    info = getattr(link, "info", None)
    if info is None:
        return "Device: not connected"
    compat = getattr(link, "compat", Compat.OK)
    hash_ok = "✗" if Compat.PARAM_HASH_MISMATCH in compat else "✓"
    feats = sorted(info.features)
    feat_txt = " ".join(f + (" (M1 placeholder samples)" if f == "AFE_SYNTHETIC" else "") for f in feats)
    return (f"Device: FW {fmt_version(info.fw_version)} (build {info.build})  proto {info.proto_major}."
            f"{info.proto_minor}  payload {info.payload_version}  dict hash 0x{info.param_dict_hash:08X} {hash_ok}  "
            f"UID {info.uid}  features {feat_txt}")


def version_line(link: Any) -> tuple[str, str]:
    """(text, level) of the version check (IF-008)."""
    if getattr(link, "info", None) is None:
        return "Version check: –", "neutral"
    compat = getattr(link, "compat", Compat.OK)
    if compat.read_only:
        return "Version check: ✗ major/payload mismatch → READ-ONLY, motion disabled", "alarm"
    if Compat.PARAM_HASH_MISMATCH in compat:
        return "Version check: ✗ dictionary hash mismatch → configuration read-only", "alarm"
    if Compat.MINOR_DIFF in compat:
        return "Version check: ✓ compatible (board protocol minor older: newer features unused)", "warn"
    return "Version check: ✓ compatible", "ok"


def link_line(status: Any) -> str:
    link = getattr(status, "link", None)
    s = getattr(link, "stats", None)
    rate = getattr(getattr(status, "stream", None), "rate_sps", None)
    state = str(getattr(getattr(link, "state", None), "value", "DISCONNECTED"))
    if s is None:
        return f"Link: {state}"
    return (f"Link: {state}  frames {s.frames_ok}  lost FW {s.frames_lost_fw} / link {s.frames_lost_link}  "
            f"dup {s.dup_frames}  seq anomalies {s.seq_anomalies}  CRC {s.crc_errors}  "
            f"timeouts {s.command_timeouts}  rate {fmt_value(rate, 'SPS')} Hz")


def cfg_line(status: Any) -> str:
    if getattr(status, "config_read_only", False):
        return "CFG: ● read-only (dictionary hash mismatch)"
    parts = []
    if getattr(status, "cfg_dirty", None):
        parts.append("dirty (RAM ≠ NVM)")
    if getattr(status, "reboot_pending", None):
        parts.append("reboot pending")
    if getattr(status, "nvm_defaulted", None):
        parts.append("NVM defaulted")
    if parts:
        return "CFG: ● " + ", ".join(parts)
    if getattr(status, "cfg_dirty", None) is None:
        return "CFG: ● ?"
    return "CFG: ● clean (RAM = NVM)"


class ConnectionTab(QWidget):
    #: (text, severity info / warn / error) for the main window's toast + Event log
    message = Signal(str, str)

    def __init__(self, backend: Any, bridge: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._bridge = bridge
        self._status: Any = None
        self.dialogs: list[Any] = []
        self.confirm_dialog: Any = None

        lay = QVBoxLayout(self)
        # ------------------------------------------------------------ connection
        conn = QGroupBox("Connection", self)
        cl = QVBoxLayout(conn)
        row = QHBoxLayout()
        row.addWidget(QLabel("Endpoint", conn))
        self.selector = EndpointSelector(backend, conn)
        row.addWidget(self.selector, 1)
        row.addWidget(QLabel("Baud 921600 (fixed)", conn))
        self.connect_button = QPushButton("Connect", conn)
        self.disconnect_button = QPushButton("Disconnect", conn)
        for b in (self.connect_button, self.disconnect_button):
            b.setAutoDefault(False)
            row.addWidget(b)
        cl.addLayout(row)
        self.device_label = QLabel("Device: not connected", conn)
        self.device_label.setWordWrap(True)
        self.device_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.version_label = QLabel("Version check: –", conn)
        lrow = QHBoxLayout()
        self.link_label = QLabel("Link: DISCONNECTED", conn)
        self.details_button = QPushButton("Details…", conn)
        self.details_button.setAutoDefault(False)
        lrow.addWidget(self.link_label, 1)
        lrow.addWidget(self.details_button)
        cl.addWidget(self.device_label)
        cl.addWidget(self.version_label)
        cl.addLayout(lrow)
        lay.addWidget(conn)

        # ------------------------------------------------------------ board configuration
        cfg = QGroupBox("Board configuration", self)
        fl = QVBoxLayout(cfg)
        top = QHBoxLayout()
        self.cfg_label = QLabel("CFG: ● ?", cfg)
        top.addStretch(1)
        top.addWidget(self.cfg_label)
        fl.addLayout(top)
        config = backend.config
        self.form = ParamForm(config.metas(), config.groups(), config.locked_keys(), cfg)
        fl.addWidget(self.form, 1)
        self.rule_label = QLabel("Rule check: ✓", cfg)
        self.rule_label.setWordWrap(True)
        fl.addWidget(self.rule_label)
        b1 = QHBoxLayout()
        self.read_button = QPushButton("Read all", cfg)
        self.write_button = QPushButton("Write && verify", cfg)
        self.revert_button = QPushButton("Revert edits", cfg)
        self.save_file_button = QPushButton("Save to file…", cfg)
        self.load_file_button = QPushButton("Load from file…", cfg)
        for b in (self.read_button, self.write_button, self.revert_button):
            b1.addWidget(b)
        b1.addSpacing(20)
        b1.addWidget(self.save_file_button)
        b1.addWidget(self.load_file_button)
        b1.addStretch(1)
        fl.addLayout(b1)
        b2 = QHBoxLayout()
        self.save_nvm_button = QPushButton("Save to NVM", cfg)
        self.load_nvm_button = QPushButton("Reload from NVM", cfg)
        self.defaults_button = QPushButton("Restore defaults…", cfg)
        self.reboot_button = QPushButton("Save && reboot…", cfg)
        for b in (self.save_nvm_button, self.load_nvm_button, self.defaults_button, self.reboot_button):
            b2.addWidget(b)
        b2.addStretch(1)
        fl.addLayout(b2)
        for b in (self.read_button, self.write_button, self.revert_button, self.save_file_button,
                  self.load_file_button, self.save_nvm_button, self.load_nvm_button, self.defaults_button,
                  self.reboot_button):
            b.setAutoDefault(False)
        lay.addWidget(cfg, 1)

        # ------------------------------------------------------------ wiring
        self.connect_button.clicked.connect(self.on_connect)
        self.disconnect_button.clicked.connect(self.on_disconnect)
        self.details_button.clicked.connect(self.open_link_stats)
        self.read_button.clicked.connect(self.on_read_all)
        self.write_button.clicked.connect(self.on_write)
        self.revert_button.clicked.connect(self.form.revert_edits)
        self.save_file_button.clicked.connect(self.on_save_file)
        self.load_file_button.clicked.connect(self.on_load_file)
        self.save_nvm_button.clicked.connect(self.on_save_nvm)
        self.load_nvm_button.clicked.connect(self.on_load_nvm)
        self.defaults_button.clicked.connect(self.on_defaults)
        self.reboot_button.clicked.connect(self.on_save_reboot)
        self._rule_timer = QTimer(self)
        self._rule_timer.setSingleShot(True)
        self._rule_timer.setInterval(RULE_DEBOUNCE_MS)
        self._rule_timer.timeout.connect(self.run_rule_check)
        self.form.valueEdited.connect(lambda _k: self._rule_timer.start())
        self.form.dirtyChanged.connect(lambda _n: self._rule_timer.start())
        bridge.paramsChanged.connect(lambda _r: self.reload_board_values())
        self.selector.refresh()
        self.reload_board_values()
        self.run_rule_check()

    # ---------------------------------------------------------------- refresh (refresh timer)
    def update_status(self, status: Any) -> None:
        self._status = status
        link = status.link
        state = str(getattr(link.state, "value", link.state))
        busy = state in ("CONNECTING",)
        connected = state in ("CONNECTED", "DEGRADED", "LOST")
        self.connect_button.setEnabled(not connected and not busy and bool(self.selector.current_endpoint()))
        self.disconnect_button.setEnabled(connected or busy)
        self.selector.setEnabled(not connected and not busy)
        t = device_line(link)
        if self.device_label.text() != t:
            self.device_label.setText(t)
        vt, level = version_line(link)
        if self.version_label.text() != vt:
            self.version_label.setText(vt)
            self.version_label.setStyleSheet({"alarm": "color: #a00000; font-weight: bold;",
                                              "warn": "color: #806000;"}.get(level, ""))
        c = cfg_line(status)
        if self.cfg_label.text() != c:
            self.cfg_label.setText(c)
        ro = bool(status.config_read_only or link.config_read_only)
        self.form.set_read_only(ro, "Dictionary hash / version mismatch – configuration read-only (IF-008)." if ro
                                else "")
        gate = gating.gate_of(status, GateId.CONFIG_WRITE)
        cs = gating.control_state(gate, motion=False)
        writable = cs.enabled and not ro
        errors = self.form.error_issues()
        dirty = bool(self.form.dirty_keys())
        self.write_button.setEnabled(writable and dirty and not errors)
        self.write_button.setToolTip(cs.tooltip if not cs.enabled else
                                     ("rule violation: " + "; ".join(i.text for i in errors) if errors else
                                      "write the edited values and verify by read-back"))
        for b in (self.save_nvm_button, self.load_nvm_button, self.defaults_button):
            b.setEnabled(writable)
            b.setToolTip(cs.tooltip)
        self.reboot_button.setEnabled(writable and bool(status.reboot_pending))
        self.read_button.setEnabled(connected)

    def update_link_line(self, status: Any) -> None:
        t = link_line(status)
        if self.link_label.text() != t:
            self.link_label.setText(t)

    # ---------------------------------------------------------------- connection
    def on_connect(self) -> None:
        ep = self.selector.current_endpoint()
        if not ep:
            self.message.emit("Select an endpoint first", "warn")
            return
        try:
            fut = self._backend.connect_async(ep)
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Connect refused: {_err_text(exc)}", "error")
            return
        self.message.emit(f"Connecting to {ep}…", "info")
        self._bridge.watch(fut, self._on_connected, lambda e: self.message.emit(
            f"Connect failed: {_err_text(e)}", "error"), "connect")

    def connect_to(self, endpoint: str) -> None:
        """Command-line start (``args.endpoint``): select and connect."""
        self.selector.set_endpoint(endpoint)
        self.on_connect()

    def _on_connected(self, info: Any) -> None:
        self.message.emit(f"Connected: FW {fmt_version(getattr(info, 'fw_version', None))}", "info")
        self.reload_board_values()

    def on_disconnect(self) -> None:
        try:
            fut = self._backend.disconnect_async()
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Disconnect failed: {_err_text(exc)}", "error")
            return
        self._bridge.watch(fut, lambda _r: self.message.emit("Disconnected", "info"),
                           lambda e: self.message.emit(f"Disconnect failed: {_err_text(e)}", "error"), "disconnect")

    def open_link_stats(self) -> None:
        dlg = LinkStatsDialog(self._backend, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialogs = [d for d in self.dialogs if _alive(d)] + [dlg]
        dlg.show()

    # ---------------------------------------------------------------- configuration
    def reload_board_values(self) -> None:
        try:
            values = self._backend.config.values()
        except Exception:  # noqa: BLE001 - not connected yet
            return
        if values:
            self.form.set_board_values(values)
            self._rule_timer.start()

    def run_rule_check(self) -> None:
        edits = self.form.edited_config()
        try:
            issues = list(self._backend.config.check(edits))
        except Exception as exc:  # noqa: BLE001
            log.warning("config.check failed", exc_info=True)
            self.rule_label.setText(f"Rule check: not available ({_err_text(exc)})")
            return
        self.form.set_issues(issues)
        errors = self.form.error_issues()
        if errors:
            text = "Rule check: ✗ " + "; ".join(f"{i.code} {i.key or ''} {i.text}".strip() for i in errors) + \
                   " → [Write & verify] disabled"
        elif issues:
            text = "Rule check: ⚠ " + "; ".join(f"{i.code} {i.text}" for i in issues)
        else:
            text = "Rule check: ✓"
        self.rule_label.setText(text)
        if self._status is not None:
            self.update_status(self._status)

    def on_read_all(self) -> None:
        self._run(self._backend.config.read_all_async, "Read all",
                  lambda values: (self.form.set_board_values(values or {}), self.run_rule_check()))

    def on_write(self) -> None:
        edits = self.form.edited_config()
        if not edits:
            self.message.emit("Nothing to write (no edited value)", "info")
            return
        self.run_rule_check()
        if self.form.error_issues():
            self.message.emit("Write refused: rule violation (see Rule check)", "warn")
            return

        def ok(report: Any) -> None:
            counts = self.form.apply_verify_report(report)
            errs = [i for i in getattr(report, "issues", ()) if str(getattr(i.severity, "value", i.severity))
                    == "ERROR"]
            summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            good = bool(getattr(report, "ok", False))
            self.message.emit(f"Write & verify: {summary}" + (" — " + "; ".join(i.text for i in errs) if errs
                                                               else ""), "info" if good else "warn")
            self.run_rule_check()

        self._run(lambda: self._backend.config.write_and_verify_async(edits), "Write & verify", ok)

    def on_save_file(self) -> None:
        path = get_save_file_name(self, "Save board configuration", "", FILE_FILTER, "bbboard.json")
        if not path:
            return
        try:
            self._backend.config.save_board_config(path, self.form.file_values())
        except Exception as exc:  # noqa: BLE001
            self.message.emit(f"Save to file failed: {_err_text(exc)}", "error")
            return
        self.message.emit(f"Board configuration saved: {path}", "info")

    def on_load_file(self) -> None:
        path = get_open_file_name(self, "Load board configuration", "", FILE_FILTER)
        if not path:
            return
        try:
            cfg = self._backend.config.load_board_config(path)
        except FileFormatError as exc:
            self._show(FileReportDialog(None, self, error=_err_text(exc)))
            self.message.emit(f"File not loaded: {_err_text(exc)}", "error")
            return
        except Exception as exc:  # noqa: BLE001
            self._show(FileReportDialog(None, self, error=_err_text(exc)))
            self.message.emit(f"File not loaded: {_err_text(exc)}", "error")
            return
        skipped = self.form.load_values(cfg.values)
        self._show(FileReportDialog(cfg, self, skipped=skipped))
        self.run_rule_check()
        self.message.emit(f"Loaded into the Edit column: {path} (not written yet)", "info")

    def _show(self, dlg: Any) -> None:
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.dialogs = [d for d in self.dialogs if _alive(d)] + [dlg]
        dlg.show()

    def on_save_nvm(self) -> None:
        self._run(self._backend.config.save_async, "Save to NVM",
                  lambda _r: self.message.emit("Saved to NVM", "info"))

    def on_load_nvm(self) -> None:
        self._run(self._backend.config.load_async, "Reload from NVM",
                  lambda values: (self.form.set_board_values(values or {}),
                                  self.message.emit("Reloaded from NVM", "info")))

    def on_defaults(self) -> None:
        dlg = make_confirm(self, "C-04")
        dlg.confirmed.connect(lambda: self._run(
            self._backend.config.defaults_async, "Restore defaults",
            lambda values: (self.form.set_board_values(values or {}),
                            self.message.emit("Defaults restored (RAM; NVM unchanged until Save)", "info"))))
        self.confirm_dialog = dlg
        dlg.open()

    def on_save_reboot(self) -> None:
        dlg = make_confirm(self, "C-11", gate_provider=lambda: gating.gate_of(self._backend.status(),
                                                                                  GateId.CONFIG_WRITE))
        dlg.confirmed.connect(self._save_then_reboot)
        self.confirm_dialog = dlg
        dlg.open()

    def _save_then_reboot(self) -> None:
        def saved(_r: Any) -> None:
            self.message.emit("Saved to NVM; rebooting the board…", "info")
            self._run(self._backend.config.reboot_async, "Reboot",
                      lambda _x: self.message.emit("Board rebooted: axis NOT homed, driver holding", "warn"))
        self._run(self._backend.config.save_async, "Save to NVM", saved)

    def _run(self, fn: Any, label: str, on_ok: Any) -> None:
        try:
            fut = fn()
        except Exception as exc:  # noqa: BLE001 - PreconditionError / NotConnected
            self.message.emit(f"{label} refused: {_err_text(exc)}", "error")
            return
        self._bridge.watch(fut, on_ok, lambda e: self.message.emit(f"{label} failed: {_err_text(e)}", "error"),
                           label)


def _alive(w: Any) -> bool:
    try:
        return w.isVisible()
    except RuntimeError:
        return False

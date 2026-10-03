"""G-04 (M1: the dialog class with C-04 and C-11, plus C-03 / C-09 / C-13 texts): Enter / Return / Space never
confirm, Esc cancels, only a mouse click confirms (after the assertion checkbox where required), STOP inside,
live gate re-check (SW_design_GUI §5.5).

Verifies: SAF-SW-004, SW-CFG-004, SW-CFG-003
"""
from __future__ import annotations

import pytest
from fakes import confirm, refuse
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from bend_stand.core.api import GATE_OK
from bend_stand.gui import stop as stop_mod
from bend_stand.gui.dialogs.confirm_dialog import TEXTS, ConfirmDialog, make_confirm

KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)


def _open(qtbot, cid: str, **kw) -> tuple[ConfirmDialog, list[str]]:
    dlg = make_confirm(None, cid, **kw)
    qtbot.addWidget(dlg)
    got: list[str] = []
    dlg.confirmed.connect(lambda: got.append("confirmed"))
    dlg.closedUnconfirmed.connect(got.append)
    dlg.open()
    qtbot.waitExposed(dlg)
    return dlg, got


@pytest.mark.req("SAF-SW-004", "SW-CFG-004", "SW-CFG-003")
@pytest.mark.parametrize("cid", sorted(TEXTS))
def test_keyboard_never_confirms(qtbot, cid) -> None:
    """Verifies: SAF-SW-004 — Return / Enter / Space on every focusable widget never confirm; no default button."""
    dlg, got = _open(qtbot, cid)
    if dlg.assertion_box is not None:
        dlg.assertion_box.setChecked(True)
    assert dlg.confirm_button.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not dlg.confirm_button.isDefault() and not dlg.confirm_button.autoDefault()
    assert not dlg.cancel_button.isDefault() and not dlg.cancel_button.autoDefault()
    assert "&" not in dlg.confirm_button.text().replace("&&", "")
    targets = [dlg] + [w for w in dlg.findChildren(QWidget) if w.focusPolicy() != Qt.FocusPolicy.NoFocus]
    for w in targets:
        for key in KEYS:
            qtbot.keyClick(w, key)
            assert dlg.outcome == "pending", (cid, type(w).__name__, key)
    assert got == []
    assert dlg.isVisible()
    dlg.reject()


@pytest.mark.req("SAF-SW-004")
@pytest.mark.parametrize("cid", ["C-04", "C-11"])
def test_mouse_click_confirms(qtbot, cid) -> None:
    """Verifies: SAF-SW-004, SW-CFG-004 (C-04), SW-CFG-003 (C-11) — a mouse click confirms exactly once."""
    dlg, got = _open(qtbot, cid)
    assert dlg.cancel_button.hasFocus() or dlg.focusWidget() is dlg.cancel_button
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert got == ["confirmed"] and dlg.outcome == "confirmed"
    assert not dlg.isVisible()


@pytest.mark.req("SAF-SW-004")
def test_escape_cancels(qtbot) -> None:
    """Verifies: SAF-SW-004 — Esc = Cancel."""
    dlg, got = _open(qtbot, "C-04")
    qtbot.keyClick(dlg, Qt.Key.Key_Escape)
    assert got == ["cancelled"] and dlg.outcome == "cancelled"


@pytest.mark.req("SAF-SW-004")
def test_assertion_checkbox_required(qtbot) -> None:
    """Verifies: SAF-SW-004 — C-03: confirm disabled until the fact is asserted; Space may tick the box."""
    dlg, got = _open(qtbot, "C-03")
    assert not dlg.confirm_button.isEnabled()
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert got == []
    dlg.assertion_box.setFocus()
    qtbot.keyClick(dlg.assertion_box, Qt.Key.Key_Space)
    assert dlg.assertion_box.isChecked() and dlg.confirm_button.isEnabled()
    assert dlg.outcome == "pending"
    qtbot.mouseClick(dlg.confirm_button, Qt.MouseButton.LeftButton)
    assert got == ["confirmed"]


@pytest.mark.req("SAF-SW-004")
def test_stop_inside_closes_unconfirmed(qtbot) -> None:
    """Verifies: SAF-SW-004 — STOP reachable inside; pressing it stops and closes without confirming."""
    calls: list[str] = []
    prev = stop_mod.set_stop_handler(calls.append)
    try:
        dlg, got = _open(qtbot, "C-11")
        qtbot.mousePress(dlg.stop_button, Qt.MouseButton.LeftButton)
        assert calls and calls[0].startswith("dialog:")
        assert got == ["stopped"] and not dlg.isVisible()
    finally:
        stop_mod.set_stop_handler(prev)


@pytest.mark.req("SAF-SW-004")
def test_live_gate_recheck(qtbot) -> None:
    """Verifies: SAF-SW-004 — CONFIRM item gone → closes "not needed"; REFUSE appears → closes with its text."""
    state = {"g": confirm("SEQUENCE_PAUSED", "ends the paused sequence")}
    dlg, got = _open(qtbot, "C-13", gate_provider=lambda: state["g"], confirm_code="SEQUENCE_PAUSED")
    dlg.recheck()
    assert dlg.outcome == "pending"
    state["g"] = GATE_OK
    dlg.recheck()
    assert got == ["not_needed"]
    state["g"] = confirm("SEQUENCE_PAUSED", "x")
    dlg2, got2 = _open(qtbot, "C-13", gate_provider=lambda: state["g"], confirm_code="SEQUENCE_PAUSED")
    state["g"] = refuse("LINK_DOWN", "not connected")
    dlg2.recheck()
    assert got2 == ["refused"] and dlg2.outcome_text == "not connected"


@pytest.mark.req("SAF-SW-004")
def test_backend_text_overrides(qtbot) -> None:
    """Verifies: SAF-SW-004 — the dialog shows the backend's CONFIRM text verbatim when given."""
    dlg, _ = _open(qtbot, "C-13", text="Backend says: ends the paused sequence")
    assert dlg.text_label.text() == "Backend says: ends the paused sequence"

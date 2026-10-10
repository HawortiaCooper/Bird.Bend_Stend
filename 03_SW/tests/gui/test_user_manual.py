"""Docs drift: the operator manual (``03_SW/docs/USER_MANUAL.md``) and the quick-reference card must name what the
GUI actually shows — every toolbar action (both states of the toggling ones), every menu action, every tab, every
indicator chip, every latch name and every Clear-stop button — and every screenshot the manual links must exist.

The texts are read from the running main window (FakeBackend, offscreen) and the display tables, not hard-coded, so
renaming a button or adding a chip / latch without updating the manual fails here.

Verifies: SAF-SW-005 (indicators and clear procedures documented for the operator), SW-STOP-002 (KL-01 stated in the
operator notes), SYS-006 (residual risk of the E-stop stated in the operator notes)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtWidgets import QAbstractButton

from bend_stand.core import protocol_gen as pg
from bend_stand.gui import indicator_map
from bend_stand.gui.dialogs.clear_stop_dialog import ClearStopDialog
from bend_stand.gui.main_window import TAB_NAMES
from bend_stand.gui.widgets.pause_button import PAUSE_TEXT, RESUME_TEXT

DOCS = Path(__file__).resolve().parents[2] / "docs"
MANUAL = DOCS / "USER_MANUAL.md"
QUICK = DOCS / "QUICK_REFERENCE.md"

#: status display widgets in the toolbar, not actions (the link text shows the link state)
NOT_ACTIONS = {"linkText"}
#: latch-type status names (FW latches + generated fault names) the operator has to clear or wait for
LATCHED_STATUS = ("ESTOP", "HALT", "PAUSED", "LIMIT_START", "LIMIT_END", "LINK_WDG", "LOAD_LIMIT")


def label(text: str) -> str:
    """Operator-facing core of a GUI label: no mnemonic '&', no leading glyphs (‖ ▶ ■ ● ☰), no trailing
    "(…)" counters / window sizes, no '…' / '▾' / '▶' decorations."""
    t = text.replace("&&", "\0").replace("&", "").replace("\0", "&")
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)
    t = t.strip(" …▾▶◀■●‖☰").strip()
    return t


def manual_text() -> str:
    return MANUAL.read_text(encoding="utf-8")


def _missing(words, text: str) -> list[str]:
    return sorted({w for w in words if w and w not in text})


@pytest.mark.req("SAF-SW-005", "SW-STOP-002", "SYS-006")
def test_manual_mentions_every_toolbar_action(window) -> None:
    """Verifies: every toolbar item (STOP, Pause / Resume, Clear stop, TARE, Stream, Record, Take sample,
    Connect / Disconnect) is named in the manual and on the quick-reference card where it is a stop or clear."""
    win = window
    texts: set[str] = set()
    for act in win.toolbar.actions():
        w = win.toolbar.widgetForAction(act)
        if w is None:
            continue
        buttons = [w] if isinstance(w, QAbstractButton) else w.findChildren(QAbstractButton)
        for b in buttons:
            if b.objectName() in NOT_ACTIONS or not b.text():
                continue
            texts.add(label(b.text()))
    texts |= {label(PAUSE_TEXT), label(RESUME_TEXT), "Connect", "Disconnect", "Stream", "Take sample"}
    assert {"STOP", "Pause", "Resume", "Clear stop", "TARE", "Record"} <= texts, texts
    assert not _missing(texts, manual_text()), _missing(texts, manual_text())
    quick = QUICK.read_text(encoding="utf-8")
    assert not _missing({"STOP", "Pause", "Resume", "Clear stop", "TARE", "Record", "Pause/Break", "E-stop"}, quick)


@pytest.mark.req("SAF-SW-005")
def test_manual_mentions_every_menu_action_and_tab(window) -> None:
    """Verifies: every menu action (File / View / Tools / Help incl. the dock toggles) and every tab name."""
    texts: set[str] = set()
    for top in window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        stack = [menu]
        while stack:
            m = stack.pop()
            for a in m.actions():
                if a.isSeparator():
                    continue
                if a.menu() is not None:
                    stack.append(a.menu())
                texts.add(label(a.text()))
    texts |= set(TAB_NAMES)
    assert {"Open session", "Exit", "New plot window", "Test Pause/Break key", "Status indicators & clear procedures"} \
        <= texts, texts
    assert not _missing(texts, manual_text()), _missing(texts, manual_text())


@pytest.mark.req("SAF-SW-005", "SYS-006")
def test_manual_mentions_every_indicator_chip_and_latch() -> None:
    """Verifies: every indicator chip label (display table) and every latch name (generated fault names + the
    latched status names) appears in the manual; the stop vocabulary and the residual-risk statements are there."""
    text = manual_text()
    chips = set(indicator_map.CHIP_LABEL.values())
    latches = {n for n in pg.FAULTS_BITS if n} | set(LATCHED_STATUS)
    generated = set(indicator_map.generated_names())
    assert set(LATCHED_STATUS) <= generated, set(LATCHED_STATUS) - generated      # names still generated
    assert not _missing(chips, text), _missing(chips, text)
    assert not _missing(latches, text), _missing(latches, text)
    for phrase in ("not an IEC 60204-1 emergency stop", "KL-01", "KL-03", "KL-05", "KL-08", "KL-09", "LOW_SPAN",
                   "NOT_REACHED", "BREAK_DETECTED", "SLIP", "no-specimen mode"):
        assert phrase in text, phrase


@pytest.mark.req("SW-STOP-003", "SAF-SW-005")
def test_manual_mentions_every_clear_stop_button(window) -> None:
    """Verifies: the Clear stop window's buttons and the E-stop assertion text are named in the manual."""
    dlg = ClearStopDialog(window.backend, window.bridge, window)
    try:
        names = {label(b.text()) for b in (dlg.halt_button, dlg.estop_button, dlg.fault_button)}
        names |= {"Clear HALT + PAUSE", "Clear PAUSE", label(dlg.estop_check.text())}
    finally:
        dlg.close()
    assert not _missing(names, manual_text()), _missing(names, manual_text())


@pytest.mark.req("SAF-SW-005")
def test_manual_images_exist() -> None:
    """Verifies: every image the manual links exists under docs/img (screenshots regenerated by
    tests/gui/manual_screenshots.py)."""
    text = manual_text()
    links = re.findall(r"\]\((img/[^)\s]+)\)", text)
    assert len(links) >= 30, links
    missing = [p for p in links if not (DOCS / p).is_file()]
    assert not missing, missing

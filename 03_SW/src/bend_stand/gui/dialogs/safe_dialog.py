"""Dialog base classes that always carry an on-screen STOP button (SW_design_GUI §5.4).

* :class:`SafeDialog`      - base of **every** dialog of the application (QDialog subclass): top bar with the hint
  "Pause/Break = HALT", the NO-SPECIMEN tag while that mode is on, and a compact STOP button.
* :class:`SafeMessageBox`  - the only way message boxes are created (STOP in the button box; a STOP press closes
  the box with "no answer"). Non-safety messages only (GF-10); safety questions use ``ConfirmDialog``.
* :class:`SafeFileDialog`  - non-native QFileDialog with STOP; the helpers :func:`get_open_file_name` /
  :func:`get_save_file_name` replace ``QFileDialog.getOpenFileName`` etc. Native dialogs are also disabled
  application-wide by :func:`disable_native_dialogs` (``gui.app.run`` calls it before the QApplication exists).

STOP never takes the focus and is never the default or the escape button.

Origin: Thrust_Stand_HAW/03_SW/src/thrust_stand/gui/dialogs/safe_dialog.py @37c87471 (changes: STOP instead of
E-STOP, NO-SPECIMEN tag in the top bar, ``hint`` text, test seams ``FILE_DIALOG_HOOK``).

Implements: SW-STOP-001 (STOP in every dialog incl. file dialogs; native dialogs off), SW-LIM-004 (NO-SPECIMEN tag)
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from bend_stand.gui.mode_state import no_specimen
from bend_stand.gui.theme import NOSPEC_TAG_STYLE
from bend_stand.gui.widgets.stop_button import StopButton

__all__ = [
    "SafeDialog", "SafeMessageBox", "SafeFileDialog", "get_open_file_name", "get_save_file_name",
    "get_existing_directory",
    "disable_native_dialogs", "FILE_DIALOG_HOOK",
]

_SB = QMessageBox.StandardButton

#: Test seam: when set, ``get_open_file_name`` / ``get_save_file_name`` return ``FILE_DIALOG_HOOK(kind, caption,
#: filter)`` instead of showing the dialog (kind = "open" | "save"). Never set by the application.
FILE_DIALOG_HOOK: list[Callable[[str, str, str], str] | None] = [None]


def disable_native_dialogs() -> None:
    """Application-wide: no native dialogs, so every file dialog carries STOP (SW-STOP-001)."""
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, True)


def native_dialogs_disabled() -> bool:
    return QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)


class NoSpecimenTag(QLabel):
    """Compact amber "NO-SPECIMEN" tag, visible while the no-specimen mode is on (§2.8)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("NO-SPECIMEN", parent)
        self.setObjectName("noSpecimenTag")
        self.setStyleSheet(NOSPEC_TAG_STYLE)
        self.setToolTip("No-specimen mode: PC load limits OFF for this session; board load limit active.")
        state = no_specimen()
        self.setVisible(state.on)
        state.changed.connect(self.setVisible)


class SafeDialog(QDialog):
    """Base class of all dialogs: a STOP bar on top, the subclass content below.

    Subclasses build their UI as usual and call ``self.setLayout(layout)`` - the layout is installed in the
    content area below the STOP bar (the outer layout is owned by this class).
    """

    def __init__(self, parent: QWidget | None = None, title: str = "") -> None:
        super().__init__(parent)
        if title:
            self.setWindowTitle(title)
        outer = QVBoxLayout()
        outer.setContentsMargins(6, 6, 6, 6)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        self._stop_hint = QLabel("Pause/Break = HALT", self)
        self._stop_hint.setStyleSheet("color: #808080;")
        bar.addWidget(self._stop_hint)
        self.nospec_tag = NoSpecimenTag(self)
        bar.addWidget(self.nospec_tag)
        bar.addStretch(1)
        self.stop_button = StopButton(self, source=f"dialog:{type(self).__name__}")
        bar.addWidget(self.stop_button)
        outer.addLayout(bar)
        self._content = QWidget(self)
        outer.addWidget(self._content, 1)
        QDialog.setLayout(self, outer)

    def setLayout(self, layout: QLayout) -> None:  # noqa: N802 - Qt naming
        """Install ``layout`` in the content area (below the STOP bar)."""
        self._content.setLayout(layout)

    def content_widget(self) -> QWidget:
        return self._content


class SafeMessageBox(QMessageBox):
    """QMessageBox with a STOP button in its button box.

    Pressing STOP stops and closes the box with "no answer" (``result_button()`` returns ``NoButton``), so no
    confirmed action is ever executed by it.
    """

    def __init__(self, icon: QMessageBox.Icon = QMessageBox.Icon.NoIcon, title: str = "",
                 text: str = "", buttons: QMessageBox.StandardButton = _SB.NoButton,
                 parent: QWidget | None = None) -> None:
        super().__init__(icon, title, text, buttons, parent)
        self.stop_button = StopButton(self, source="messagebox")
        self.addButton(self.stop_button, QMessageBox.ButtonRole.ActionRole)
        self.stop_button.pressed.connect(self._on_stop)

    def _on_stop(self) -> None:
        self._stopped = True
        self.done(0)

    def _other_buttons(self) -> list[QAbstractButton]:
        return [b for b in self.buttons() if b is not self.stop_button]

    def _fix_buttons(self) -> None:
        others = self._other_buttons()
        if not others:
            self.addButton(_SB.Ok)
            others = self._other_buttons()
        if self.defaultButton() is None or self.defaultButton() is self.stop_button:
            for b in others:
                if self.buttonRole(b) in (QMessageBox.ButtonRole.AcceptRole, QMessageBox.ButtonRole.YesRole):
                    self.setDefaultButton(b)  # type: ignore[arg-type]
                    break
        esc = self.escapeButton()
        if esc is None or esc is self.stop_button:
            pick = None
            for b in others:
                if self.buttonRole(b) in (QMessageBox.ButtonRole.RejectRole, QMessageBox.ButtonRole.NoRole):
                    pick = b
                    break
            if pick is None and len(others) == 1:
                pick = others[0]
            if pick is not None:
                self.setEscapeButton(pick)
        self.stop_button.setDefault(False)
        self.stop_button.setAutoDefault(False)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._fix_buttons()
        super().showEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Esc = the (non-STOP) escape button, or close; never routed to STOP."""
        if event.matches(QKeySequence.StandardKey.Cancel):
            self._fix_buttons()
            esc = self.escapeButton()
            if esc is not None and esc is not self.stop_button:
                esc.click()
            else:
                self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def result_button(self) -> QMessageBox.StandardButton:
        """Standard button that closed the box; ``NoButton`` if STOP (or nothing) was pressed."""
        if getattr(self, "_stopped", False):
            return _SB.NoButton
        clicked = self.clickedButton()
        if clicked is None or clicked is self.stop_button:
            return _SB.NoButton
        return self.standardButton(clicked)

    @classmethod
    def show_message(cls, parent: QWidget | None, title: str, text: str,
                     icon: QMessageBox.Icon = QMessageBox.Icon.Information) -> "SafeMessageBox":
        """Non-blocking information box (the GUI thread never waits in a nested loop for a message)."""
        box = cls(icon, title, text, _SB.Ok, parent)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        box.setModal(False)
        box.show()
        return box


class SafeFileDialog(QFileDialog):
    """Non-native (Qt widget) file dialog with a STOP button."""

    def __init__(self, parent: QWidget | None = None, caption: str = "", directory: str = "",
                 filter: str = "") -> None:  # noqa: A002 - Qt naming
        super().__init__(parent, caption, directory, filter)
        self.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.stop_button = StopButton(self, source="filedialog")
        lay = self.layout()
        if isinstance(lay, QGridLayout):
            lay.addWidget(self.stop_button, lay.rowCount(), 0, 1, lay.columnCount(), Qt.AlignmentFlag.AlignLeft)
        elif lay is not None:
            lay.addWidget(self.stop_button)


def get_open_file_name(parent: QWidget | None, caption: str, directory: str = "",
                       filter: str = "") -> str:  # noqa: A002
    """Replacement for ``QFileDialog.getOpenFileName`` (non-native, with STOP). "" = cancelled."""
    hook = FILE_DIALOG_HOOK[0]
    if hook is not None:
        return hook("open", caption, filter)
    dlg = SafeFileDialog(parent, caption, directory, filter)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dlg.setFileMode(QFileDialog.FileMode.ExistingFile)
    try:
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selectedFiles():
            return dlg.selectedFiles()[0]
        return ""
    finally:
        dlg.deleteLater()


def get_save_file_name(parent: QWidget | None, caption: str, directory: str = "",
                       filter: str = "", default_suffix: str = "") -> str:  # noqa: A002
    """Replacement for ``QFileDialog.getSaveFileName`` (non-native, with STOP). "" = cancelled."""
    hook = FILE_DIALOG_HOOK[0]
    if hook is not None:
        return hook("save", caption, filter)
    dlg = SafeFileDialog(parent, caption, directory, filter)
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dlg.setFileMode(QFileDialog.FileMode.AnyFile)
    if default_suffix:
        dlg.setDefaultSuffix(default_suffix)
    try:
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selectedFiles():
            return dlg.selectedFiles()[0]
        return ""
    finally:
        dlg.deleteLater()


def get_existing_directory(parent: QWidget | None, caption: str, directory: str = "") -> str:
    """Folder chooser (non-native, with STOP; D-54 c recordings folder). "" = cancelled. Test seam:
    ``FILE_DIALOG_HOOK("dir", caption, "")``."""
    hook = FILE_DIALOG_HOOK[0]
    if hook is not None:
        return hook("dir", caption, "")
    dlg = SafeFileDialog(parent, caption, directory, "")
    dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    dlg.setFileMode(QFileDialog.FileMode.Directory)
    dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
    try:
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selectedFiles():
            return dlg.selectedFiles()[0]
        return ""
    finally:
        dlg.deleteLater()

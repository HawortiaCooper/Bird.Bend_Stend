"""``SchemaForm``: a parameter form generated from a backend ``GeneratorSchema`` (SW_design_GUI §6.5, A-20).

``GeneratorSchema(name, label, fields: FieldSpec(name, label, unit, kind float / int / enum / bool, min, max,
default, choices, depends_on, srs))`` — min / max / default are resolved by the backend at call time (motion
limits, SW limits, session defaults), so **no range is duplicated in the GUI**. ``depends_on`` shows a field only
while another field has a value: ``("field", value)`` / ``("field", (v1, v2))`` / ``{"field": value}`` /
``"field=value"`` / ``"field"`` (truthy). Hidden fields are not passed to the generator.

A generator ``ValueError`` is shown verbatim (:meth:`show_error`); the field whose name or label the text names is
marked red.

Implements: SW-WIZ-001 (generator parameters from the backend schema), SW-WIZ-002
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bend_stand.gui.seq_access import get, text_of

ERR_STYLE = "background-color: #ffc8c8;"
BIG = 1e9


def _choices(spec: Any) -> list[tuple[Any, str]]:
    out = []
    for c in get(spec, "choices", default=()) or ():
        if isinstance(c, (tuple, list)) and len(c) == 2:
            out.append((c[0], str(c[1])))
        else:
            out.append((c, text_of(c)))
    return out


def _decimals(spec: Any) -> int:
    unit = str(get(spec, "unit", default="") or "")
    return {"mm": 3, "mm/s": 3, "mm/s²": 1, "mm/s2": 1, "N": 2, "s": 2, "kgf": 3, "N/mm": 2}.get(unit, 3)


class SchemaForm(QWidget):
    changed = Signal()

    def __init__(self, schema: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.schema = schema
        self.setObjectName(f"schemaForm_{get(schema, 'name', default='')}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.form = QFormLayout()
        outer.addLayout(self.form)
        self.error_label = QLabel("", self)
        self.error_label.setObjectName("schemaError")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #a00000;")
        self.error_label.hide()
        outer.addWidget(self.error_label)
        outer.addStretch(1)
        self.specs: list[Any] = list(get(schema, "fields", default=()) or ())
        self.editors: dict[str, QWidget] = {}
        self.labels: dict[str, QLabel] = {}
        for spec in self.specs:
            name = str(get(spec, "name"))
            unit = str(get(spec, "unit", default="") or "")
            label = QLabel(str(get(spec, "label", default=name)) + (f" [{unit}]" if unit else ""), self)
            ed = self._editor(spec)
            ed.setObjectName(f"field_{name}")
            tip = str(get(spec, "srs", default="") or "")
            if tip:
                ed.setToolTip(tip)
            self.form.addRow(label, ed)
            self.editors[name] = ed
            self.labels[name] = label
        self.update_visibility()

    # ------------------------------------------------------------------ editors
    def _editor(self, spec: Any) -> QWidget:
        kind = text_of(get(spec, "kind", default="float")).lower()
        default = get(spec, "default")
        lo, hi = get(spec, "min"), get(spec, "max")
        if kind == "bool":
            w: Any = QCheckBox(self)
            w.setChecked(bool(default))
            w.toggled.connect(self._on_change)
            return w
        if kind == "enum" or _choices(spec):
            w = QComboBox(self)
            for value, label in _choices(spec):
                w.addItem(label, value)
            if default is not None:
                i = next((k for k in range(w.count()) if w.itemData(k) == default or w.itemText(k) ==
                          text_of(default)), -1)
                if i >= 0:
                    w.setCurrentIndex(i)
            w.currentIndexChanged.connect(self._on_change)
            return w
        if kind == "int":
            w = QSpinBox(self)
            w.setRange(int(lo) if lo is not None else -2**31 + 1, int(hi) if hi is not None else 2**31 - 1)
            if default is not None:
                w.setValue(int(default))
            w.valueChanged.connect(self._on_change)
            return w
        w = QDoubleSpinBox(self)
        w.setDecimals(_decimals(spec))
        w.setRange(float(lo) if lo is not None and math.isfinite(float(lo)) else -BIG,
                   float(hi) if hi is not None and math.isfinite(float(hi)) else BIG)
        if default is not None:
            w.setValue(float(default))
        w.valueChanged.connect(self._on_change)
        return w

    def _on_change(self, *_a: Any) -> None:
        self.update_visibility()
        self.clear_error()
        self.changed.emit()

    # ------------------------------------------------------------------ values
    def raw_value(self, name: str) -> Any:
        w = self.editors[name]
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QComboBox):
            return w.currentData()
        return w.value()

    def set_value(self, name: str, value: Any) -> None:
        w = self.editors[name]
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QComboBox):
            i = next((k for k in range(w.count()) if w.itemData(k) == value or w.itemText(k) == text_of(value)), -1)
            if i >= 0:
                w.setCurrentIndex(i)
        else:
            w.setValue(value)

    def visible(self, name: str) -> bool:
        spec = next((s for s in self.specs if str(get(s, "name")) == name), None)
        return spec is None or self._dep_ok(get(spec, "depends_on"))

    def _dep_ok(self, dep: Any) -> bool:
        if not dep:
            return True
        conds: list[tuple[str, Any]] = []
        if isinstance(dep, Mapping):
            conds = list(dep.items())
        elif isinstance(dep, str):
            for part in dep.split(","):
                k, _s, v = part.partition("=")
                conds.append((k.strip(), v.strip() if _s else None))
        elif isinstance(dep, (tuple, list)) and len(dep) == 2 and isinstance(dep[0], str):
            conds = [(dep[0], dep[1])]
        elif isinstance(dep, (tuple, list)):
            for d in dep:
                if not self._dep_ok(d):
                    return False
            return True
        for k, want in conds:
            if k not in self.editors:
                continue
            have = self.raw_value(k)
            if want is None:
                if not have:
                    return False
            elif isinstance(want, (tuple, list, set, frozenset)):
                if have not in want and text_of(have) not in {text_of(w) for w in want}:
                    return False
            elif have != want and text_of(have) != text_of(want):
                return False
        return True

    def update_visibility(self) -> None:
        for name, w in self.editors.items():
            vis = self.visible(name)
            w.setVisible(vis)
            self.labels[name].setVisible(vis)

    def values(self) -> dict[str, Any]:
        """The visible fields' values (hidden fields are not passed)."""
        return {n: self.raw_value(n) for n in self.editors if self.visible(n)}

    # ------------------------------------------------------------------ errors
    def show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.show()
        low = text.lower()
        for name, w in self.editors.items():
            lab = self.labels[name].text().split(" [")[0].lower()
            hit = name.lower() in low or (lab and lab in low)
            w.setStyleSheet(ERR_STYLE if hit else "")

    def clear_error(self) -> None:
        if self.error_label.isVisible() or self.error_label.text():
            self.error_label.setText("")
            self.error_label.hide()
            for w in self.editors.values():
                w.setStyleSheet("")

    def error_text(self) -> str:
        return self.error_label.text()

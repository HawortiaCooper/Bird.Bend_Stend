"""PySide6 + pyqtgraph GUI of the Bird Bend Stand (SW_design_GUI v0.3).

Owner: Implementer D (D-29 n). Entry point: :func:`bend_stand.gui.app.run` (B3-20). The GUI holds no business or
safety logic (P1): it calls the backend facade (``core.api`` Protocols) and displays its state verbatim.

Imports allowed in this package (G-01): ``bend_stand.core.api``, ``bend_stand.core.protocol_gen`` (name tables),
``bend_stand.calc.units`` and the package root ``bend_stand`` (``__version__`` for the About box).

Implements: SW-PLT-001, SW-PLT-002
"""
from __future__ import annotations

__all__: list[str] = []

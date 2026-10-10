"""Render backend of the plot panes: software raster (default) or an OpenGL viewport (NFR-009, D-54 b;
SW_design_GUI §4.8).

* The choice is a GUI preference (View ▸ OpenGL rendering, ``ui/opengl`` in ``gui.ini``); the environment variable
  ``BEND_STAND_GUI_OPENGL`` (``1`` / ``0``) overrides it (perf runs, support).
* OpenGL = pyqtgraph's ``GraphicsView.useOpenGL(True)``: the pane's viewport becomes a ``QOpenGLWidget``
  (``GraphicsViewGLWidget``); Qt's own GL functions, no PyOpenGL. Items still paint through ``QPainter`` (Qt's GL
  paint engine).
* **Fallback:** if switching fails, or the GL viewport is not valid once shown (no context: driver, remote session,
  offscreen), the pane goes back to the raster viewport, OpenGL is marked *failed* for the process (every pane,
  also new ones, uses raster) and :data:`STATE` keeps the reason for the View menu / Event log.
* Default **off**: on the DEV PC (GTX 1080 Ti, Windows, PySide6 6.11) the GL viewport made the 600 s × 4-window
  layout and the NFR-001 layout 3–5 × slower (one GL-composited window per pane, §4.8).

Implements: NFR-009 (render backend selectable, fallback), NFR-001 (raster default)
"""
from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

ENV = "BEND_STAND_GUI_OPENGL"

#: process-wide render state (GUI thread only)
STATE: dict[str, Any] = {"opengl": False, "failed": False, "reason": ""}


def env_override() -> bool | None:
    v = os.environ.get(ENV, "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    return None


def wanted(setting_value: Any) -> bool:
    """OpenGL requested: the environment override, else the stored preference (default off)."""
    env = env_override()
    if env is not None:
        return env
    return str(setting_value).strip().lower() in ("1", "true", "on", "yes")


def set_opengl(on: bool) -> None:
    STATE["opengl"] = bool(on)


def active() -> bool:
    return bool(STATE["opengl"]) and not STATE["failed"]


def _is_gl(vp: Any) -> bool:
    return type(vp).__name__ == "GraphicsViewGLWidget" or hasattr(vp, "makeCurrent")


def mark_failed(reason: str) -> None:
    if not STATE["failed"]:
        log.warning("OpenGL plot rendering unavailable, software raster used: %s", reason)
    STATE["failed"] = True
    STATE["reason"] = reason


def apply(plot_widget: Any) -> str:
    """Give ``plot_widget`` (a pyqtgraph GraphicsView) the viewport of the current mode; returns "opengl" / "raster".
    A failing switch falls back to raster (and marks OpenGL failed)."""
    want_gl = active()
    vp = plot_widget.viewport()
    if want_gl and not _is_gl(vp):
        try:
            plot_widget.useOpenGL(True)
        except Exception as exc:  # noqa: BLE001 - any GL problem → raster
            mark_failed(f"useOpenGL failed: {exc}")
            _raster(plot_widget)
            return "raster"
    elif not want_gl and _is_gl(vp):
        _raster(plot_widget)
    return "opengl" if _is_gl(plot_widget.viewport()) else "raster"


def _raster(plot_widget: Any) -> None:
    try:
        plot_widget.useOpenGL(False)
    except Exception:  # noqa: BLE001
        log.exception("switching back to the raster viewport failed")


def check(plot_widget: Any) -> bool:
    """After the first show: a GL viewport without a valid context → raster + OpenGL failed. True = OK."""
    vp = plot_widget.viewport()
    if not _is_gl(vp):
        return True
    try:
        ok = bool(vp.isValid()) and vp.context() is not None
    except Exception as exc:  # noqa: BLE001
        ok = False
        STATE["reason"] = str(exc)
    if not ok:
        mark_failed(STATE["reason"] or "no valid OpenGL context")
        _raster(plot_widget)
    return ok


def xy_points(px_width: int) -> int:
    """X-Y pane point budget: ≈ 2 points per pixel column (min / max), 400…4000 (NFR-009: the backend decimation
    cost grows with the budget; more points than columns are not visible)."""
    return int(max(400, min(4000, 2 * max(0, int(px_width)))))


__all__ = ["ENV", "STATE", "env_override", "wanted", "set_opengl", "active", "apply", "check", "mark_failed",
           "xy_points"]

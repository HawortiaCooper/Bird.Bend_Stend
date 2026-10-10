"""F-B-PKG-01: the application icon is a package resource (``bend_stand/gui/resources/BirdBendStand.ico``), so the
dev run and the frozen build show it instead of Qt's default; it stays identical to B's packaging asset.

Verifies: SW-PLT-001 (application start: own window / taskbar icon)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bend_stand.gui import resources

PACKAGING_ICON = Path(__file__).resolve().parents[2] / "packaging" / "assets" / "BirdBendStand.ico"


@pytest.mark.req("SW-PLT-001")
def test_icon_resource_present_and_loadable(qapp) -> None:
    """Verifies: SW-PLT-001 — the resource file exists inside the package and loads as a non-null QIcon."""
    assert resources.ICON_PATH.is_file()
    assert resources.ICON_PATH.parent.name == "resources" and resources.ICON_PATH.parent.parent.name == "gui"
    icon = resources.app_icon()
    assert not icon.isNull() and icon.availableSizes()


@pytest.mark.req("SW-PLT-001")
def test_icon_resource_matches_packaging_asset() -> None:
    """Verifies: SW-PLT-001 — the package copy equals B's packaging asset (exe icon == window icon)."""
    if not PACKAGING_ICON.is_file():
        pytest.skip("packaging asset not present in this checkout")
    assert resources.ICON_PATH.read_bytes() == PACKAGING_ICON.read_bytes()


@pytest.mark.req("SW-PLT-001")
def test_main_window_icon_not_null(window) -> None:
    """Verifies: SW-PLT-001 — the main window carries the icon (also without an app-wide icon, e.g. in tests)."""
    assert not window.windowIcon().isNull()

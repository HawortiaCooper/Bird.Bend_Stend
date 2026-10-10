"""PyInstaller entry script of the frozen application (``BirdBendStand.exe`` / ``BirdBendStand-cli.exe``).

Same behaviour as ``python -m bend_stand`` (``bend_stand.__main__.main``): ``--sim``, ``--port``, ``--session``,
``--headless``, ``--version``; without ``--port`` / ``--sim`` the GUI starts disconnected (D-06).

Implements: SW-PLT-001
"""
from __future__ import annotations

import sys

from bend_stand.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

"""Data-safety rule (Orchestrator, incident 2026-10-14): test / simulator helpers never touch the operator's folders.
``bbs_support.lockstep_backend()`` with no arguments, run with no test environment (as a scratch script would), must
create or modify nothing under the real ``%APPDATA%`` / ``Documents`` — here replaced by sentinel folders.

Verifies: SW-PLT-001 (test infrastructure must not alter user data), SW-BRU-002, SW-ACQ-002
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from bbs_support import lockstep_backend

from bend_stand.core import paths
from bend_stand.core.testing import isolated_settings

ENVS = ("BEND_STAND_DATA_DIR", "BEND_STAND_RECORDINGS_DIR", "BEND_STAND_BRINGUP_DIR", "BEND_STAND_GUI_SETTINGS")


def _sentinel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "sentinel_home"
    (home / "AppData" / "Roaming").mkdir(parents=True)
    (home / "Documents").mkdir()
    for e in ENVS:
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
    assert paths.app_data_dir() == home / "AppData" / "Roaming" / "BirdBendStand"      # what the app would use
    assert paths.default_recordings_root() == home / "Documents" / "BirdBendStand" / "recordings"
    return home


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")}


@pytest.mark.req("SW-PLT-001", "SW-BRU-002", "SW-ACQ-002")
def test_helper_without_arguments_never_touches_the_user_folders(tmp_path, monkeypatch) -> None:
    home = _sentinel(tmp_path, monkeypatch)
    before = _snapshot(home)
    be = lockstep_backend()
    try:
        h = be.test_hooks
        assert Path(be.data_dir).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
        assert be.record_start().ok                                  # recordings
        h.advance(300)
        assert be.record_stop().ok
        be.session.save(str(Path(be.data_dir) / "sessions" / "default.bbsession.json"))
        assert be.bringup.start().ok                                 # bring-up session + package
        assert be.bringup.mark(be.bringup.steps()[-1].id, "PASS").ok
        pkg = h.result(be.bringup.create_package_async(), 30_000)
        assert not Path(pkg.path).resolve().is_relative_to(home.resolve())
        assert Path(be.reports.root()).resolve().is_relative_to(Path(be.data_dir).resolve())
    finally:
        be.shutdown()
    assert _snapshot(home) == before, "the helper wrote into the sentinel user folders"
    assert not (home / "AppData" / "Roaming" / "BirdBendStand").exists()
    assert not (home / "Documents" / "BirdBendStand").exists()


@pytest.mark.req("SW-PLT-001")
def test_isolated_settings_rules(tmp_path, monkeypatch) -> None:
    home = _sentinel(tmp_path, monkeypatch)
    a, b = isolated_settings(), isolated_settings()
    assert a["data_dir"] != b["data_dir"]                            # a fresh temp folder per call
    assert a["recordings_root"] == str(Path(a["data_dir"]) / "recordings")
    assert a["bringup_root"] == str(Path(a["data_dir"]) / "bringup_packages")
    assert not str(home) in a["data_dir"]
    explicit = isolated_settings(data_dir=str(tmp_path / "d"), recordings_root=str(tmp_path / "r"))
    assert explicit["data_dir"] == str(tmp_path / "d") and explicit["recordings_root"] == str(tmp_path / "r")
    monkeypatch.setenv("BEND_STAND_DATA_DIR", str(tmp_path / "env"))                  # the pytest conftest folders
    assert isolated_settings()["data_dir"] == str(tmp_path / "env")
    assert isolated_settings(real_user_data=True, seq_seed=1) == {"seq_seed": 1}      # explicit opt-in only
    for d in (a, b):
        os.rmdir(d["data_dir"]) if not any(Path(d["data_dir"]).iterdir()) else None

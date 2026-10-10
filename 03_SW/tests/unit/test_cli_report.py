"""Entry point additions for the installed application (OI-UM-03): ``report`` subcommand (offline report rebuild through
the public ``bend_stand.core.report.main``), ``--recordings DIR`` and ``--headless --record``.

Verifies: SW-PLT-001, SW-REP-003, SW-ACQ-001
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bend_stand import __main__ as entry
from bend_stand.core import report


@pytest.mark.req("SW-PLT-001", "SW-REP-003")
@pytest.mark.parametrize("argv", [["report", "rec"], ["report", "rec", "--out", "o", "--tare-raw", "5"], ["report"]])
def test_report_subcommand_delegates_to_public_entry(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(report, "main", lambda a=None: seen.append(list(a)) or 7)
    assert entry.main(argv) == 7
    assert seen == [argv[1:]]


@pytest.mark.req("SW-PLT-001", "SW-REP-003")
def test_report_subcommand_missing_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["report", str(tmp_path / "nothing")]) == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.req("SW-PLT-001", "SW-REP-003")
def test_help_names_report_subcommand_and_recordings(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        entry.main(["--help"])
    out = capsys.readouterr().out
    assert "subcommand: report <recording folder>" in out and "--recordings DIR" in out and "--record" in out


@pytest.mark.req("SW-PLT-001")
def test_recordings_option_reaches_backend_settings(tmp_path: Path) -> None:
    args = entry.parse_args(["--sim", "--recordings", str(tmp_path)])
    assert entry.backend_settings(args).recordings_root == str(tmp_path)
    assert entry.backend_settings(entry.parse_args([])).recordings_root is None


@pytest.mark.req("SW-PLT-001", "SW-ACQ-001", "SW-REP-003")
@pytest.mark.rt
def test_headless_record_then_offline_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rec_root = tmp_path / "rec"
    assert entry.main(["--headless", "--sim", "--duration", "1.0", "--record", "--recordings", str(rec_root)]) == 0
    out = capsys.readouterr().out
    m = re.search(r"^recording: (.+?)  rows (\d+)", out, re.M)
    assert m and int(m.group(2)) > 0
    folder = Path(m.group(1))
    assert folder.parent == rec_root and (folder / "data.csv").is_file() and (folder / "meta.json").is_file()
    assert entry.main(["report", str(folder), "--out", str(tmp_path / "rep")]) == 0
    printed = capsys.readouterr().out.split()
    assert str(tmp_path / "rep" / "report.json") in printed and str(tmp_path / "rep" / "report.html") in printed
    doc = json.loads((tmp_path / "rep" / "report.json").read_text(encoding="utf-8"))
    assert doc["schema"] == "bird.bend.report" and doc["summary"]["rows"] > 0

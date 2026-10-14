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


@pytest.mark.req("SW-PLT-001", "SW-BRU-002", "SW-BRU-003")
@pytest.mark.rt
def test_headless_bringup_mark_then_export_and_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """``--headless --bringup-mark <step>`` opens a session with the step PASS and closes it; ``bringup-export``
    packs it from the same data folder; ``--verify`` accepts the zip (smoke S10 from the source tree)."""
    from bend_stand.core import bringup

    monkeypatch.setenv("BEND_STAND_DATA_DIR", str(tmp_path / "data"))
    step = bringup.load_definitions().ids[0]
    assert entry.main(["--headless", "--sim", "--duration", "0.5", "--bringup-mark", step]) == 0
    m = re.search(r"^bring-up session: (\S+)\s+passed (\d+)/(\d+)", capsys.readouterr().out, re.M)
    assert m and int(m.group(2)) == 1
    assert entry.main(["bringup-export", "--out", str(tmp_path / "out")]) == 0
    zips = list((tmp_path / "out").glob("*.zip"))
    assert len(zips) == 1
    capsys.readouterr()
    assert entry.main(["bringup-export", "--verify", str(zips[0])]) == 0
    assert "package OK" in capsys.readouterr().out


@pytest.mark.req("SW-PLT-001", "SW-BRU-002")
@pytest.mark.rt
def test_headless_bringup_mark_unknown_step_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("BEND_STAND_DATA_DIR", str(tmp_path / "data"))
    assert entry.main(["--headless", "--sim", "--duration", "0.2", "--bringup-mark", "no-such-step"]) == 1
    assert "bring-up mark no-such-step refused" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ --bringup-mark integrity

@pytest.mark.req("SW-BRU-002", "SW-PLT-001")
@pytest.mark.parametrize("argv, needle", [
    (["--headless", "--port", "COM7", "--bringup-mark", "6.3"], "refused with --port COM7"),
    (["--headless", "--port", "tcp://127.0.0.1:5760", "--bringup-mark", "6.3"], "refused with --port tcp://"),
    (["--headless", "--bringup-mark", "6.3"], "refused with --port (none)"),
    (["--sim", "--bringup-mark", "6.3"], "needs --headless --sim"),
])
def test_bringup_mark_refused_outside_headless_sim(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                                   argv: list[str], needle: str) -> None:
    """``--bringup-mark`` marks steps without an operator: refused (exit 2, clear message) unless the run is headless
    on the in-process simulator — before any backend is built, so no port is ever touched."""
    import bend_stand.core.backend as backend_mod

    def no_backend(*_a, **_k):
        raise AssertionError("a Backend was built for a refused --bringup-mark run")

    monkeypatch.setattr(backend_mod, "Backend", no_backend)
    assert entry.main(argv) == 2
    err = capsys.readouterr().err
    assert needle in err and "simulator" in err


@pytest.mark.req("SW-BRU-002")
def test_bringup_mark_refusal_rule() -> None:
    ok = entry.parse_args(["--headless", "--sim", "--bringup-mark", "1"])
    assert entry.bringup_mark_refusal(ok) is None
    assert entry.bringup_mark_refusal(entry.parse_args(["--headless", "--sim=x.simscn.json", "--bringup-mark", "1"])) \
        is None
    assert entry.bringup_mark_refusal(entry.parse_args(["--headless", "--port", "COM3"])) is None   # no mark: n/a


def _package_texts(zip_path: Path) -> tuple[str, dict, dict]:
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        return (zf.read("report.html").decode("utf-8"), json.loads(zf.read("bringup.json")),
                json.loads(zf.read("session/session.json")))


def _mark_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
                     ) -> tuple[str, str, dict, dict]:
    from bend_stand.core import bringup

    monkeypatch.setenv("BEND_STAND_DATA_DIR", str(tmp_path / "data"))
    step = bringup.load_definitions().ids[0]
    assert entry.main(["--headless", "--sim", "--duration", "0.3", "--bringup-mark", step]) == 0
    assert entry.main(["bringup-export", "--out", str(tmp_path / "out")]) == 0
    capsys.readouterr()
    html, record, session = _package_texts(next((tmp_path / "out").glob("*.zip")))
    return step, html, record, session


@pytest.mark.req("SW-BRU-002", "SW-BRU-003")
@pytest.mark.rt
def test_cli_mark_is_visibly_a_command_line_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    """The marked step carries "marked from the command line (test)" in session.json, bringup.json and the HTML."""
    step, html, record, session = _mark_and_export(tmp_path, monkeypatch, capsys)
    assert entry.CLI_TEST_REMARK == "marked from the command line (test)"
    rec = next(s for s in record["steps"] if s["id"] == step)
    assert rec["state"] == "PASS" and rec["manual"] is True and rec["remark"] == entry.CLI_TEST_REMARK
    assert session["steps"][step]["remark"] == entry.CLI_TEST_REMARK
    assert entry.CLI_TEST_REMARK in html


@pytest.mark.req("SW-BRU-002", "SW-BRU-003")
@pytest.mark.rt
def test_cli_mark_origin_field_is_cli_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    """Structured origin: the step record (session.json and bringup.json) says origin = "cli-test"; operator marks
    stay "operator"."""
    step, html, record, session = _mark_and_export(tmp_path, monkeypatch, capsys)
    assert session["steps"][step]["origin"] == entry.CLI_TEST_ORIGIN == "cli-test"
    assert next(s for s in record["steps"] if s["id"] == step)["origin"] == "cli-test"
    assert "marked from the command line (test)" in html

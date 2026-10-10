"""Fixes of Validator F's code review (``03_SW/docs/SW_code_review.md`` v1.1) owned by B (packaging / report / log):

* SWR-11 — report HTML: sidecar values type-coerced and escaped, script-free CSP;
* SWR-14 — application log file (rotating ``<data>/logs/bend_stand.log``) + incident log for every run;
* SWR-23 — formula injection in ``samples.csv`` neutralised;
* SWR-24 — offline calibration override validated (schema, ``validate_load``, finite numbers), non-PASS warned.

Verifies: SW-REP-001, SW-REP-003, SW-ACQ-003, SW-PLT-001, SAF-SW-005
"""
from __future__ import annotations

import json
import logging
import math
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bend_stand.core import logfile
from bend_stand.core import report as R
from bend_stand.core.errors import FileFormatError
from bend_stand.core.events import EventBus
from bend_stand.core.model import FwEvent, LinkState, LinkStateChange, SampleRow, StopConfirmation, TestMarks
from bend_stand.core.recorder import COLUMNS, SAMPLE_COLUMNS, append_sample, csv_cell

CAL_FILE = {"schema": "bird.bend.cal.load", "schema_version": 1, "created_utc": "2026-10-05T00:00:00Z",
            "afe": {"type": "HX711", "channel": "A", "gain": 128, "rate_sps": 80},
            "points": [{"force_n": 0.0, "raw_mean": 50_000.0}, {"force_n": 392.266, "raw_mean": 1_338_000.0}],
            "fit": {"k_n_per_count": 3.0e-4, "status": "PASS"}}


def _recording(folder: Path, meta: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"schema": "bird.bend.recording", "schema_version": 1,
                                                  **(meta or {})}), encoding="utf-8")
    (folder / "data.csv").write_text("# x\n" + ",".join(COLUMNS) + "\n", encoding="utf-8")
    return folder


# ============================================================================================ SWR-11
@pytest.mark.req("SW-REP-001", "SW-REP-003")
def test_swr11_result_from_dict_coerces_types() -> None:
    r = R.result_from_dict({"exec_idx": "3", "step_idx": 1.0, "uid": 7, "label": None, "kind": "load",
                            "n": "<script>", "trim_iterations": "x", "f_mean_n": "abc", "target": "1e2",
                            "flags": "NOT_A_LIST", "loop_iters": [1, "2", "z"], "window_s": [0, "1.5"],
                            "stats": "x", "text": 5})
    assert (r.exec_idx, r.step_idx, r.uid, r.label, r.n, r.trim_iterations) == (3, 1, "7", "", 0, 0)
    assert math.isnan(r.f_mean_n) and r.target == 100.0 and r.flags == () and r.loop_iters == (1, 2)
    assert r.window_s == (0.0, 1.5) and r.stats == {} and r.text == "5"
    with pytest.raises(FileFormatError, match="exec_idx"):
        R.result_from_dict({"exec_idx": "<b>", "step_idx": 0, "uid": "u", "label": "", "kind": ""})
    with pytest.raises(FileFormatError):
        R.result_from_dict(["not", "a", "mapping"])  # type: ignore[arg-type]
    again = R.result_from_dict(R.result_to_dict(r))                           # round trip unchanged (NaN-safe)
    assert repr(R.result_to_dict(again)) == repr(R.result_to_dict(r))


@pytest.mark.req("SW-REP-001", "SW-REP-003")
def test_swr11_report_html_has_no_raw_markup_and_a_csp(tmp_path: Path) -> None:
    evil = "<script>alert(1)</script>"
    res = {"exec_idx": 0, "step_idx": 0, "uid": evil, "label": evil, "kind": evil, "unit": evil, "n": evil,
           "flags": [evil], "text": evil, "target": evil}
    folder = _recording(tmp_path / "r", {
        "marks_final": {"specimen": evil, "custom": [[evil, evil]]}, "sw_version": evil, "fw_version": evil,
        "sequence_runs": [{"sequence": {"name": evil}, "state": evil, "end_reason": evil, "message": evil,
                           "windows": [], "results": [res]}]})
    page = Path(R.build_report(folder).html).read_text(encoding="utf-8")
    assert "<script" not in page and "alert(1)</" not in page
    assert "Content-Security-Policy" in page and "default-src 'none'" in page
    assert "&lt;script&gt;" in page                                           # shown as text


@pytest.mark.req("SW-REP-001")
def test_swr11_index_cells() -> None:
    assert R._idx1(0) == "1" and R._idx1("4") == "5" and R._idx1("<b>") == "&lt;b&gt;"  # noqa: SLF001


# ============================================================================================ SWR-24
def _cal_file(tmp_path: Path, name: str, data: object) -> str:
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(p)


@pytest.mark.req("SW-REP-003")
@pytest.mark.parametrize("mutate, match", [
    (lambda d: d.update(schema=5), "not a bird.bend.cal.load file"),
    (lambda d: (d.pop("schema"), d["fit"].update(status="FAIL")), "cannot be active"),   # bare record: validated
    (lambda d: d.update(schema="bird.bend.cal.travel"), "not a bird.bend.cal.load file"),
    (lambda d: d.update(schema_version=99), "newer SW version"),
    (lambda d: d["fit"].update(k_n_per_count=0), "non-zero"),
    (lambda d: d["fit"].update(k_n_per_count="abc"), "k_n_per_count"),
    (lambda d: d["fit"].update(status="FAIL"), "cannot be active"),
    (lambda d: d["points"][1].update(force_n="x"), "load calibration record invalid"),
    (lambda d: d["points"].pop(), "at least two points"),
    (lambda d: d["afe"].update(rate_sps=-80), "rate_sps"),
    (lambda d: d.pop("afe"), "afe"),
    (lambda d: d.update(created_utc=5), "created_utc"),
])
def test_swr24_invalid_calibration_file_is_refused(tmp_path: Path, mutate, match: str) -> None:
    d = json.loads(json.dumps(CAL_FILE))
    mutate(d)
    path = _cal_file(tmp_path, "cal.json", d)
    folder = _recording(tmp_path / "r")
    with pytest.raises(FileFormatError, match=match):
        R.build_report(folder, cal=path)
    assert R.main([str(folder), "--cal", path]) == 2


@pytest.mark.req("SW-REP-003")
def test_swr24_bare_record_file_accepted_when_valid(tmp_path: Path) -> None:
    """A calibration record without the store header (e.g. copied from a recording's meta.json snapshot) is applied
    when it passes the same checks."""
    d = {k: v for k, v in json.loads(json.dumps(CAL_FILE)).items() if k not in ("schema", "schema_version")}
    folder = _recording(tmp_path / "r")
    R.build_report(folder, cal=_cal_file(tmp_path, "bare.json", d))
    assert R.load_result(folder).calibration["k_n_per_count"] == pytest.approx(3.0e-4)
    with pytest.raises(FileFormatError, match="not a JSON object"):
        R.build_report(folder, cal=_cal_file(tmp_path, "list.json", [1, 2]))


@pytest.mark.req("SW-REP-003")
def test_swr24_non_finite_numbers_refused(tmp_path: Path) -> None:
    folder = _recording(tmp_path / "r")
    text = json.dumps(CAL_FILE).replace('"raw_mean": 50000.0', '"raw_mean": NaN')
    with pytest.raises(FileFormatError, match="not finite"):
        R.build_report(folder, cal=_cal_file(tmp_path, "nan.json", text))
    text = json.dumps(CAL_FILE).replace("0.0003", "Infinity")
    with pytest.raises(FileFormatError, match="k_n_per_count"):
        R.build_report(folder, cal=_cal_file(tmp_path, "inf.json", text))
    with pytest.raises(FileFormatError, match="calibration file"):
        R.build_report(folder, cal=_cal_file(tmp_path, "deep.json", "[" * 100_000 + "]" * 100_000))


@pytest.mark.req("SW-REP-003")
@pytest.mark.parametrize("status, warned", [("PASS", False), ("WARN", True), ("UNVERIFIED_LINEARITY", True)])
def test_swr24_valid_file_applied_and_non_pass_warned(tmp_path: Path, status: str, warned: bool) -> None:
    d = json.loads(json.dumps(CAL_FILE))
    d["fit"]["status"] = status
    folder = _recording(tmp_path / "r")
    R.build_report(folder, cal=_cal_file(tmp_path, "cal.json", d))
    res = R.load_result(folder)
    assert res.calibration["k_n_per_count"] == pytest.approx(3.0e-4) and res.calibration["override"] is True
    assert ("applied calibration status " + status + " (not PASS)" in res.warnings) is warned


@pytest.mark.req("SW-REP-003")
def test_swr24_in_process_record_number_checks(tmp_path: Path) -> None:
    folder = _recording(tmp_path / "r")
    with pytest.raises(FileFormatError, match="non-zero"):
        R.build_report(folder, cal={"fit": {"k_n_per_count": 0.0, "status": "PASS"}})
    with pytest.raises(FileFormatError, match="not finite"):
        R.build_report(folder, cal={"fit": {"k_n_per_count": float("nan"), "status": "PASS"}})
    R.build_report(folder, cal={"fit": {"k_n_per_count": 1e-4, "status": "PASS"},
                                "points": [{"force_n": 0.0, "raw_mean": 1.0}, {"force_n": 1.0, "raw_mean": 2.0}]})


# ============================================================================================ SWR-23
@pytest.mark.req("SW-ACQ-003")
@pytest.mark.parametrize("text, cell", [
    ("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-2+3", "'-2+3"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"),
    ("\r=x", ";=x"), ("plain", "plain"), ("A-1", "A-1"), ("", ""), ("a,b\nc", "a;b;c"), ("=a,b", "'=a;b"),
])
def test_swr23_csv_cell_neutralises_formulas(text: str, cell: str) -> None:
    assert csv_cell(text) == cell


@pytest.mark.req("SW-ACQ-003")
def test_swr23_samples_csv_marks_are_not_formulas(tmp_path: Path) -> None:
    marks = TestMarks(specimen="=HYPERLINK(\"http://x\")", number="+1", operator="@op", notes="-2",
                  custom=(("k", "=v"),))
    row = SampleRow("2026-10-09T00:00:00Z", 1.0, 1.0, 80, 1.0, 0.1, 80, 2.0, 0.01, 3.0, 0.2, 80, marks)
    p = tmp_path / "samples.csv"
    append_sample(p, row)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(SAMPLE_COLUMNS)
    cells = lines[1].split(",")
    assert cells[-5:] == ["'=HYPERLINK(\"http://x\")", "'+1", "'@op", "'-2", "k==v"]   # custom: "k=v" pairs
    assert not any(c[:1] in "=+-@" for c in cells[-5:])


# ============================================================================================ SWR-14
@pytest.fixture
def clean_logging():
    yield
    logfile.shutdown_logging()
    if hasattr(sys, logfile.STDIO_REDIRECT_ATTR):
        delattr(sys, logfile.STDIO_REDIRECT_ATTR)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.mark.req("SW-PLT-001")
def test_swr14_log_file_in_data_dir_and_idempotent(tmp_path: Path, clean_logging) -> None:
    s1 = logfile.setup_logging("WARNING", data_dir=tmp_path)
    s2 = logfile.setup_logging("WARNING", data_dir=tmp_path)                # second set-up replaces the first
    assert s1.path == s2.path == tmp_path / "logs" / "bend_stand.log"
    ours = logfile.our_handlers()
    assert len(ours) == 2 and sum(isinstance(h, logging.StreamHandler) and not isinstance(
        h, logging.handlers.QueueHandler) for h in ours) == 1                  # one console + one queue handler
    logging.getLogger("bend_stand.test").info("info-record")
    logging.getLogger("bend_stand.test").debug("debug-record")
    logfile.shutdown_logging()
    text = _read(s2.path)
    assert "info-record" in text and "debug-record" not in text and text.count("log start") == 2
    assert logfile.our_handlers() == []


@pytest.mark.req("SW-PLT-001")
def test_swr14_frozen_redirect_gets_no_console_handler(tmp_path: Path, clean_logging) -> None:
    """OI-F-RV-04: under the frozen windowed exe (stderr already redirected by the runtime hook) the set-up adds
    no console handler — every record is written once, to bend_stand.log."""
    setattr(sys, logfile.STDIO_REDIRECT_ATTR, str(tmp_path / "BirdBendStand.log"))
    st = logfile.setup_logging("INFO", data_dir=tmp_path)
    assert not st.console and len(logfile.our_handlers()) == 1
    assert isinstance(logfile.our_handlers()[0], logging.handlers.QueueHandler)


@pytest.mark.req("SW-PLT-001")
def test_swr14_rotation_bounds_the_size(tmp_path: Path, clean_logging) -> None:
    st = logfile.setup_logging("WARNING", data_dir=tmp_path, max_bytes=2000, backups=2)
    lg = logging.getLogger("bend_stand.test")
    for i in range(200):
        lg.warning("record %04d %s", i, "x" * 60)
    logfile.shutdown_logging()
    files = sorted(p.name for p in (tmp_path / "logs").iterdir())
    assert files == ["bend_stand.log", "bend_stand.log.1", "bend_stand.log.2"]
    assert all(p.stat().st_size <= 2200 for p in (tmp_path / "logs").iterdir())
    assert "record 0199" in _read(st.path)


@pytest.mark.req("SW-PLT-001")
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_swr14_uncaught_thread_exception_logged_and_hooks_restored(tmp_path: Path, clean_logging) -> None:
    prev_t, prev_s = threading.excepthook, sys.excepthook
    st = logfile.setup_logging("CRITICAL", data_dir=tmp_path)
    assert threading.excepthook is not prev_t and sys.excepthook is not prev_s

    def boom() -> None:
        raise RuntimeError("thread-boom")

    t = threading.Thread(target=boom, name="bbs-test-thread")
    t.start()
    t.join()
    logfile.shutdown_logging()
    assert threading.excepthook is prev_t and sys.excepthook is prev_s
    text = _read(st.path)
    assert "uncaught exception in thread bbs-test-thread" in text and "RuntimeError: thread-boom" in text


@pytest.mark.req("SW-PLT-001")
def test_swr14_read_only_location_falls_back_to_console(tmp_path: Path, clean_logging) -> None:
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x", encoding="utf-8")
    st = logfile.setup_logging("WARNING", data_dir=blocker)                  # logs/ cannot be created under a file
    assert st.path is None and st.console


@pytest.mark.req("SW-PLT-001", "SAF-SW-005")
def test_swr14_incidents_logged(tmp_path: Path, clean_logging) -> None:
    st = logfile.setup_logging("CRITICAL", data_dir=tmp_path)
    bus = EventBus()
    logfile.attach_incident_log(bus)
    bus.publish("link.state", LinkStateChange(LinkState.CONNECTED, "", "sim"))
    bus.publish("link.state", LinkStateChange(LinkState.LOST, "rx timeout", "COM7"))
    bus.publish("stop.issued", SimpleNamespace(cmd="STOP", source="gui", sent=True, error=None, reason=""))
    bus.publish("stop.unconfirmed", StopConfirmation("HALT", "hotkey", 3, 0, False))
    bus.publish("fw.event", FwEvent(5, 1000, 3, "ESTOP_SET", 0, None, 0, 0))
    bus.publish("fw.event", FwEvent(6, 2000, 2, "MOVE_DONE", 0, None, 0, 0))
    bus.publish("safety.trip", SimpleNamespace(limit="PULL", value=105.0, threshold=100.0, unit="N", text=""))
    bus.publish("rec.failure", SimpleNamespace(failure="ENOSPC", folder="r1"))
    for state in ("RUNNING", "RUNNING", "ABORTED"):
        bus.publish("seq.status", SimpleNamespace(state=state, end_reason="HALT" if state == "ABORTED" else None))
    bus.publish("device.status", object())                                   # not an incident topic
    logfile.shutdown_logging()
    lines = [ln for ln in _read(st.path).splitlines() if "bend_stand.incident" in ln]
    text = "\n".join(lines)
    assert re.search(r"INFO .*link CONNECTED sim", text) and re.search(r"WARNING .*link LOST COM7 \(rx timeout\)", text)
    assert re.search(r"INFO .*STOP issued by gui: written", text)
    assert re.search(r"ERROR .*HALT \(hotkey\) NOT CONFIRMED after 3", text)
    assert re.search(r"WARNING .*FW EVENT ESTOP_SET", text) and re.search(r"INFO .*FW EVENT MOVE_DONE", text)
    assert re.search(r"WARNING .*PC limit trip PULL", text) and re.search(r"ERROR .*recording failed: ENOSPC", text)
    assert text.count("sequence RUNNING") == 1 and re.search(r"WARNING .*sequence ABORTED HALT", text)
    assert len(lines) == 10


@pytest.mark.req("SW-PLT-001", "SAF-SW-005")
@pytest.mark.rt
def test_swr14_entry_point_writes_the_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    from bend_stand import __main__ as entry

    monkeypatch.setenv("BEND_STAND_DATA_DIR", str(tmp_path / "data"))
    assert entry.main(["--headless", "--sim", "--duration", "0.5"]) == 0
    assert logfile.our_handlers() == [] and logfile.current() is None       # shut down after main()
    text = _read(tmp_path / "data" / "logs" / "bend_stand.log")
    assert "log start" in text and "link CONNECTED sim" in text and "FW EVENT BOOT" in text
    assert "link DISCONNECTED" in text

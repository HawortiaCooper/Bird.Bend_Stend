"""Validator E - unit tests of the HIL package (no hardware, no twin): D-06 interlock, §6.1 rule-4 decisions,
DWT evaluation, DIAG_MEAS tables vs protocol.yaml, stamp paging under drift, report rendering.

    .venv\\Scripts\\python -m pytest 00_System\\tools\\hil\\tests -q

Verifies: (harness for) SYS-009 HW gate, D-06 (interlock), FW_test_plan §6.1 rule 4
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest
import yaml

HIL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIL))

import hil_budget as hb  # noqa: E402
import hil_link as hl  # noqa: E402
import hil_report  # noqa: E402

TODAY = dt.date(2026, 10, 12)


# ------------------------------------------------------------------------------------------- D-06 interlock
@pytest.fixture
def repo(tmp_path):
    (tmp_path / "00_System" / "specs").mkdir(parents=True)
    (tmp_path / "00_System" / "specs" / "DECISIONS.md").write_text("| D-45 | PO approved HW gate D-06-GATE-20261012 |",
                                                                    encoding="utf-8")
    (tmp_path / "00_System" / "STATUS.md").write_text("status", encoding="utf-8")
    return tmp_path


class Opener:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return object()


@pytest.mark.parametrize("ref, why", [
    (None, "no PO approval"), ("", "no PO approval"), ("D06-GATE-20261012", "does not match"),
    ("D-06-GATE-20261312", "no valid date"), ("D-06-GATE-20261013", "future"), ("D-06-GATE-20260901", "older than"),
    ("D-06-GATE-20261011", "not recorded"),
])
def test_interlock_refuses_without_valid_recorded_approval(repo, ref, why):
    op = Opener()
    with pytest.raises(hl.InterlockError, match=why):
        hl.open_serial("COM7", ref, today=TODAY, confirm=lambda p: True, opener=op, repo=repo)
    assert op.calls == []                                  # the port was never opened


def test_interlock_needs_port_and_confirmation(repo):
    op = Opener()
    for port, conf, why in ((None, lambda p: True, "no port"), ("COM7", None, "confirmation"),
                            ("COM7", lambda p: False, "did not confirm")):
        with pytest.raises(hl.InterlockError, match=why):
            hl.open_serial(port, "D-06-GATE-20261012", today=TODAY, confirm=conf, opener=op, repo=repo)
    assert op.calls == []


def test_interlock_opens_only_with_everything(repo):
    op = Opener()
    seen = []
    # only the exact recorded reference counts (a tagged variant is a different reference)
    with pytest.raises(hl.InterlockError, match="not recorded"):
        hl.open_serial("COM7", "D-06-GATE-20261012-HG1", today=TODAY, confirm=lambda p: True, opener=op, repo=repo)
    hl.open_serial("COM7", "D-06-GATE-20261012", today=TODAY, confirm=lambda p: seen.append(p) or True, opener=op,
                   repo=repo)
    assert seen == ["COM7"] and len(op.calls) == 1
    kw = op.calls[0]
    assert kw["port"] == "COM7" and kw["baudrate"] == 921600 and kw["parity"] == "N" and kw["stopbits"] == 1
    assert kw["rtscts"] is False and kw["dsrdtr"] is False


def test_session_cli_refuses_port_without_approval(monkeypatch, capsys, tmp_path):
    import hil_session
    import serial
    opened = []
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: opened.append((a, k)))
    for args in (["--port", "COM7"], ["--port", "COM7", "--approved", "D-06-GATE-29991231"],
                 ["--port", "COM7", "--approved", "not-a-reference"]):
        assert hil_session.main(args + ["--out", str(tmp_path / "s")]) == 2
    assert opened == []
    assert "REFUSED" in capsys.readouterr().err
    with pytest.raises(SystemExit):                       # --twin and --port are exclusive
        hil_session.main(["--twin", "--port", "COM7", "--out", str(tmp_path / "s")])


def test_real_repo_has_no_recorded_approval_today():
    """D-06 is in force: no D-06-GATE reference is recorded yet, so a board session cannot start."""
    txt = "".join((hl.REPO / f).read_text(encoding="utf-8") for f in hl.APPROVAL_FILES)
    assert "D-06-GATE-" not in txt


# ------------------------------------------------------------------------------------------- rule 4
@pytest.mark.parametrize("v, b, u, want", [(98.9, 100, 1, hb.PASS), (99.5, 100, 1, hb.INCONCL),
                                           (101.5, 100, 1, hb.FAIL), (100, 100, 0, hb.PASS)])
def test_decide_le(v, b, u, want):
    assert hb.decide_le(v, b, u) == want


@pytest.mark.parametrize("v, l_, u, want", [(10.02, 10, 0.011, hb.PASS), (10.0, 10, 0.011, hb.INCONCL),
                                            (9.9, 10, 0.011, hb.FAIL)])
def test_decide_ge(v, l_, u, want):
    assert hb.decide_ge(v, l_, u) == want


def test_check_le_uses_max_and_stats():
    c = hb.check_le("x", [10, 50, 30], 100, 1.1, "µs")
    assert c.value == 50 and c.decision == hb.PASS and c.n == 3 and c.stats["median"] == 30
    assert hb.check_le("x", [], 100, 1, "µs").decision == hb.NOT_MEASURED


def test_verdict_order():
    P, F, I, M, N = (hb.Check("a", "", d) for d in (hb.PASS, hb.FAIL, hb.INCONCL, hb.MANUAL, hb.NA))
    assert hb.verdict([P, F, I, M]) == hb.FAIL
    assert hb.verdict([P, I, M]) == hb.INCONCL
    assert hb.verdict([P, M, N]).startswith("PARTIAL")
    assert hb.verdict([M]).startswith("OPEN")
    assert hb.verdict([P, N]) == hb.PASS
    assert hb.verdict([N]) == hb.NA


def test_all_eq_reports_mismatch():
    c = hb.check_all_eq("n", [(5, 5), (7, 6)])
    assert c.decision == hb.FAIL and "(1, 7, 6)" in c.note


def test_conversions():
    assert hb.probe_tick_us(17) == pytest.approx(0.1)
    assert hb.probe_tick_us(1799) == pytest.approx(10.0)
    assert hb.sdiff32(5, 0xFFFFFFFE) == 7 and hb.sdiff32(0xFFFFFFFE, 5) == -7
    assert hb.uart_frame_us(9, 921600) == pytest.approx(97.656, abs=1e-3)


def test_dwt_f2_evaluation():
    """F2: step ISR ≤ 2 µs (360 cycles), E-stop handler ≤ 1 µs (180 cycles), overhead subtracted (section 21)."""
    cal = hb.DwtSection(21, True, 16, 10, 12, 16 * 11, [16] + [0] * 9)
    ovh = hb.dwt_overhead(cal)
    assert ovh == (11.0, 2.0)
    step = hb.DwtSection.from_words(1, [1, 1000, 250, 370, 300_000, 0] + [0] * 10)
    c = hb.check_dwt("step", step, 2.0, ovh)          # (370 − 11) cyc = 1.994 µs, u = 2 cyc -> inconclusive
    assert c.decision == hb.INCONCL
    est = hb.DwtSection.from_words(2, [1, 20, 100, 160, 2400, 0] + [0] * 10)
    assert hb.check_dwt("estop", est, 1.0, ovh).decision == hb.PASS        # raw max 0.889 µs
    bad = hb.DwtSection.from_words(2, [1, 20, 100, 207, 2400, 0] + [0] * 10)
    assert hb.check_dwt("estop", bad, 1.0, ovh).decision == hb.FAIL         # (207 − 11) cyc = 1.089 µs
    assert hb.check_dwt("x", hb.DwtSection.from_words(2, [0] * 16), 1.0, ovh).decision == hb.NOT_MEASURED
    assert hb.check_dwt("x", hb.DwtSection.from_words(2, [1] + [0] * 15), 1.0, ovh).decision == hb.NOT_MEASURED


# ------------------------------------------------------------------------------------------- tables
def test_meas_tables_match_protocol_yaml():
    y = yaml.safe_load((hl.REPO / "00_System" / "specs" / "protocol.yaml").read_text(encoding="utf-8"))
    tables = {t["id"]: t for t in y["tables"]}

    def enum(tid):
        return {i["name"]: i.get("value", i.get("bit")) for i in tables[tid]["items"]}
    assert enum("meas_op") == hl.MEAS_OP
    assert enum("meas_src") == hl.MEAS_SRC
    assert enum("meas_probe_mode") == hl.MEAS_MODE
    assert enum("meas_probe_flags") == hl.MEAS_PF
    assert enum("meas_chan") == hl.MEAS_CHAN
    assert enum("meas_hang_where") == hl.MEAS_HANG
    assert enum("meas_pin") == hl.MEAS_PIN
    assert enum("meas_variant") == hl.MEAS_VAR


# ------------------------------------------------------------------------------------------- stamp paging
class FakeMeas(hl.Meas):
    """Ring of stamps t = 1000 + 10·entry; every request adds `drift` new stamps first (J-EVT on the RX line)."""

    def __init__(self, n0: int, drift: int, ring: int = 2048):
        self.n, self.drift, self.ring, self.reads = n0, drift, ring, 0

    def stamps_page(self, chan, page=0):
        self.n += self.drift
        self.reads += 1
        vals = []
        for k in range(hl.STAMPS_PER_PAGE):
            back = page * hl.STAMPS_PER_PAGE + k
            vals.append(1000 + 10 * (self.n - 1 - back) if back < min(self.n, self.ring) else 0)
        return self.n, self.ring, vals


@pytest.mark.parametrize("drift", [0, 1, 16, 60])
def test_stamp_entries_under_drift(drift):
    m = FakeMeas(500, drift)
    got = m.stamp_entries("EVT", 100, 105)
    assert got == {e: 1000 + 10 * e for e in range(100, 106)}
    assert m.reads < 40


def test_stamp_entries_overwritten_and_future():
    m = FakeMeas(5000, 0)
    assert m.stamp_entries("PUL", 10, 10) == {10: None}            # overwritten (ring 2048)
    assert m.stamp_entries("PUL", 6000, 6000) == {6000: None}      # not written yet


# ------------------------------------------------------------------------------------------- report
def test_report_renders(tmp_path):
    (tmp_path / "results").mkdir()
    (tmp_path / "session.json").write_text(json.dumps({"mode": "twin", "order": ["HG-09"], "approval": "x"}))
    it = {"id": "HG-09", "title": "Step count", "req": "SAF-FW-004", "method": "m", "verdict": "PASS",
          "checks": [hb.check_eq("n", 3, 3).to_dict(), hb.manual("caliper", "≤ u", 1.0, "dry-run default").to_dict()],
          "notes": ["a|b"], "data": {}, "error": "", "t_start": "", "t_end": "",
          "operator": [{"key": "CAL0", "prompt": "Caliper", "value": 1.0, "source": "dry-run default (not evidence)"}]}
    (tmp_path / "results" / "HG-09.json").write_text(json.dumps(it))
    p = hil_report.write(tmp_path)
    txt = p.read_text(encoding="utf-8")
    assert "TWIN DRY RUN" in txt and "not hardware evidence" in txt.replace("**", "")
    assert "| HG-09 | Step count | **PASS** |" in txt and "a\\|b" in txt and "What stays open" in txt

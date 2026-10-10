"""Validator E - code review FWR (02_FW/docs/FW_code_review.md): reproducers and regression tests for the HIL
interlock / gating findings FWR-12, FWR-13, FWR-15 and the HIL part of FWR-09. No hardware, no twin, no serial port
(D-06): the link, operator and repository are fakes / tmp dirs.

    .venv\\Scripts\\python -m pytest 00_System\\tools\\hil\\tests\\test_hil_review.py -q

Each test states the SAFE behaviour. At e600169 the FWR-12 / FWR-13 tests failed (finding evidence); the FWR-15 /
FWR-09 tests were added with the fix (v0.2 of the review).
Verifies: SYS-009, D-06 (interlock), FW_test_plan §6.8 (gate prerequisites are evidence of this board session)
TC: TC-SYS-009-03
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HIL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIL))

import hil_link as hl  # noqa: E402

REF = "D-06-GATE-20261012"
UID = "3400451A3133510237363934"


# ------------------------------------------------------------------------------------------- FWR-12
@pytest.mark.parametrize("row", [
    f"| {REF} | 2026-10-12 | PO has not approved the HW gate yet | open |",
    f"| {REF} | 2026-10-12 | HW gate | approved: no |",
    f"| {REF} | 2026-10-12 | HW gate | NOT approved |",
    f"| {REF} | 2026-10-12 | HW gate | approved? to be confirmed by the PO |",
])
def test_fwr12_negated_or_questioned_approval_is_not_an_approval(row):
    """R-HIL-02 intent: only a row whose status says 'approved' counts. A negated / questioned status must not."""
    assert hl.approval_row(row, REF) is None, "approval_row() accepts a row that does not approve (FWR-12)"


@pytest.mark.parametrize("row", [
    f"| {REF} | 2026-10-12 | PO did not object | approved (PO) |",          # negation elsewhere in the row
    f"| {REF} | 2026-10-12 | HW gate, approval tbd | approved |",
    f"| {REF} | 2026-10-12 | HW gate | approved (PO) | revoked |",            # status is the LAST cell
    f"| 2026-10-12 | {REF} | HW gate | approved (PO) |",                      # reference must be the first cell
])
def test_fwr12_dedicated_row_format_only(row):
    assert hl.approval_row(row, REF) is None


def test_fwr12_positive_row_still_accepted():
    row = f"| `{REF}` | 2026-10-12 | PO approved HW gate: board UID …, COM7 | approved (PO) |"
    assert hl.approval_row(row, REF) == row


# ------------------------------------------------------------------------------------------- helpers
class FakeOp:
    def __init__(self):
        self.item = None

    def take_log(self, _i):
        return []


def _ctx(tmp_path: Path, twin: bool = False, board_uid: str | None = UID, link=None, session=None):
    import hil_procs as P
    link = link or SimpleNamespace(is_twin=twin)
    cfg = dict(P.DEFAULT_CFG)
    cfg["accepted_open"] = {}
    cfg["board_uid"] = board_uid
    return P.Ctx(link, FakeOp(), cfg, tmp_path, seed=1, session=session)


def _info(**kw) -> dict:
    import hil_procs as P
    inf = {"param_dict_hash": P.DICT_HASH, "proto_major": 1, "proto_minor": 0, "payload_version": 1, "uid": UID,
           "build": "261012-0900-MEAS", "features": ["AFE", "MOTION", "HW_MEAS"]}
    inf.update(kw)
    return inf


# ------------------------------------------------------------------------------------------- FWR-13
def test_fwr13_load_gate_refuses_results_not_produced_on_the_board(tmp_path):
    """SYS-009: the load gate needs HG-01…24, 28, 29, 32 PASS on the BOARD. Result files of a twin dry run (or of
    an older session) left in the same --out directory carry no mode / session identity and must not open it."""
    import hil_procs as P
    import hil_session as S
    res = tmp_path / "results"
    res.mkdir(parents=True)
    for i in S.LOAD_PREREQ:                                     # as written by `hil_session.py --twin --out <dir>`
        (res / f"{i}.json").write_text(json.dumps({"id": i, "verdict": "PASS", "checks": [], "error": ""}),
                                       encoding="utf-8")
    ctx = _ctx(tmp_path)
    ctx.begin("GATE-LOAD", "SYS-009 gate", "SYS-009", "")
    with pytest.raises(P.BenchRefused):
        S.gate_load(ctx)                                        # e600169: passes -> motion under load allowed


def test_fwr13_results_are_bound_to_session_board_and_image(tmp_path):
    import hil_budget as hb
    ctx = _ctx(tmp_path)
    ctx.session["images"]["meas"] = "261012-0900-MEAS|" + str(_info()["param_dict_hash"])
    ctx.image = "meas"
    ctx.begin("HG-11", "limit", "SAF-FW-002", "")
    ctx.check(hb.check_true("x", True, "x"))
    ctx.end()
    assert ctx.result_ok("HG-11")
    other = _ctx(tmp_path)                                      # a new session in the same directory
    other.session["images"] = dict(ctx.session["images"])
    assert other.result_of("HG-11") is None
    same_other_board = _ctx(tmp_path, board_uid="FFFF451A3133510237363934",
                            session=dict(ctx.session, board_uid="FFFF451A3133510237363934"))
    assert same_other_board.result_of("HG-11") is None
    twin = _ctx(tmp_path, twin=True, session=dict(ctx.session, mode="twin"))
    assert twin.result_of("HG-11") is None
    no_image = _ctx(tmp_path, session=dict(ctx.session, images={"meas": "other-build|0x0"}))
    assert no_image.result_of("HG-11") is None                  # image not verified in this session


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "00_System" / "specs").mkdir(parents=True)
    (tmp_path / "00_System" / "specs" / "DECISIONS.md").write_text(
        "| RR-HG-12-01 | 2026-10-12 | HG-12 load-limit timing accepted open (§6.6 R-4) | accepted (PO) |\n"
        "| RR-HG-25-01 | 2026-10-12 | HG-25 speed envelope accepted open | proposed |\n"
        "| D-60 | 2026-10-12 | HG-26 home repeatability not accepted | accepted |\n", encoding="utf-8")
    (tmp_path / "00_System" / "STATUS.md").write_text("status", encoding="utf-8")
    return tmp_path


def test_fwr13_accepted_open_reference_recorded(repo):
    assert hl.check_accepted_open("HG-12", "RR-HG-12-01", repo=repo)
    for item, ref in (("HG-25", "RR-HG-25-01"),             # proposed, not accepted
                      ("HG-26", "D-60"),                    # negated
                      ("HG-13", "RR-HG-12-01"),             # row does not name HG-13
                      ("HG-12", "anything goes"),           # not a reference id
                      ("XX-12", "RR-HG-12-01")):            # not an HG id
        with pytest.raises(hl.InterlockError):
            hl.check_accepted_open(item, ref, repo=repo)


# ------------------------------------------------------------------------------------------- FWR-15
def test_fwr15_port_session_needs_board_uid(monkeypatch, capsys, tmp_path):
    import hil_session
    import serial
    opened = []
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: opened.append((a, k)))
    for extra in ([], ["--board-uid", "1234"], ["--board-uid", "not-hex-not-hex-not-hex!"]):
        assert hil_session.main(["--port", "COM7", "--approved", REF, "--out", str(tmp_path / "s")] + extra) == 2
    assert opened == []
    assert "--board-uid" in capsys.readouterr().err


@pytest.mark.parametrize("only, from_, want", [
    ("HG-11", None, ["S-00", "HG-11"]),
    (None, "HG-28", None),                                      # S-00 first, then HG-28 … in plan order
    ("HG-10a", None, ["S-00", "BENCH-ENTRY", "HG-10a", "BENCH-EXIT"]),
    ("HG-12", None, ["S-00", "GATE-LOAD", "HG-12"]),
])
def test_fwr15_only_and_from_cannot_skip_identity_or_bench(tmp_path, only, from_, want):
    import hil_session as S
    ids = [s.id for s in S.select_steps(_ctx(tmp_path), only, from_)]
    assert ids[0] == "S-00"
    if want:
        assert ids == want
    else:
        assert ids[1] == "HG-28" and ids.count("S-00") == 1
        k = ids.index("HG-10a")
        assert ids[k - 1] == "BENCH-ENTRY" and ids[k + 1] == "BENCH-EXIT"


def test_fwr15_unknown_step_id_refused(tmp_path):
    import hil_session as S
    with pytest.raises(ValueError):
        S.select_steps(_ctx(tmp_path), "HG-99", None)
    with pytest.raises(ValueError):
        S.select_steps(_ctx(tmp_path), None, "HG-99")


@pytest.mark.parametrize("bad", [
    {"param_dict_hash": "0x12345678"},
    {"proto_major": 2},
    {"uid": "FFFF451A3133510237363934"},
])
def test_fwr15_identity_mismatch_refuses(tmp_path, bad):
    import hil_procs as P
    link = SimpleNamespace(is_twin=False, info=lambda: _info(**bad))
    ctx = _ctx(tmp_path, link=link)
    ctx.begin("S-00", "start")
    with pytest.raises(P.BenchRefused):
        P.check_identity(ctx)
    assert ctx.session["images"] == {}


def test_fwr15_identity_ok_records_image(tmp_path):
    import hil_procs as P
    link = SimpleNamespace(is_twin=False, info=lambda: _info(build="261012-0900-DWT"))
    ctx = _ctx(tmp_path, link=link)
    ctx.begin("S-00", "start")
    P.check_identity(ctx)
    assert ctx.image == "meas_dwt" and ctx.session["images"]["meas_dwt"].startswith("261012-0900-DWT|")


# ------------------------------------------------------------------------------------------- FWR-09 (HIL part)
def test_fwr09_hg06_disables_even_in_not_enabled(tmp_path):
    """NOT_ENABLED after boot keeps ENA at the holding level (D-13): HG-06 must send DISABLE anyway and must not drive
    PUL statically unless ENA reads disabled."""
    import hil_procs as P
    sent = []
    meas = SimpleNamespace(static_level=lambda *a: sent.append(("STATIC_LEVEL", a)) or {"status": "OK"})
    link = SimpleNamespace(is_twin=False, meas=meas, ok=lambda c, *a: sent.append((c, a)),
                           status=lambda: {"motion_state": "NOT_ENABLED", "io": []}, cmd=lambda *a, **k: {})
    ctx = _ctx(tmp_path, link=link)
    ctx.begin("HG-06", "opto")
    with pytest.raises(P.BenchRefused):
        P.hg06(ctx)                                             # ENA does not read disabled -> refused
    assert ("DISABLE", ()) in sent and not any(c == "STATIC_LEVEL" for c, _ in sent)


# =========================================================================================== Integrator review (§9)
# C-R1: a later / other row for the same reference that does not approve it cancels the approval
@pytest.mark.parametrize("later", [
    f"| {REF} | 2026-10-13 | PO withdrew the approval | revoked |",
    f"| {REF} | withdrawn |",
    f"| **{REF}** | 2026-10-13 | gate postponed | pending |",
])
def test_cr1_later_revocation_cancels_approval(later):
    text = f"| {REF} | 2026-10-12 | PO approved HW gate | approved (PO) |\n{later}\n"
    assert hl.approval_row(text, REF) is None


def test_cr1_revocation_in_the_other_file_cancels(tmp_path):
    import datetime as dt
    today = dt.date(2026, 10, 12)
    (tmp_path / "00_System" / "specs").mkdir(parents=True)
    (tmp_path / "00_System" / "specs" / "DECISIONS.md").write_text(
        f"| {REF} | 2026-10-12 | PO approved HW gate | approved (PO) |\n", encoding="utf-8")
    (tmp_path / "00_System" / "STATUS.md").write_text(f"| {REF} | 2026-10-12 | withdrawn by the PO | revoked |\n",
                                                       encoding="utf-8")
    with pytest.raises(hl.InterlockError):
        hl.check_approval(REF, today=today, repo=tmp_path)


def test_cr1_accepted_open_revoked_later(repo):
    d = repo / "00_System" / "specs" / "DECISIONS.md"
    d.write_text(d.read_text(encoding="utf-8") + "| RR-HG-12-01 | 2026-10-13 | HG-12 acceptance withdrawn | revoked |\n",
                 encoding="utf-8")
    with pytest.raises(hl.InterlockError):
        hl.check_accepted_open("HG-12", "RR-HG-12-01", repo=repo)


# C-R2: example rows in fenced code blocks / HTML comments are not records
@pytest.mark.parametrize("wrap", [
    "```\n{row}\n```", "~~~markdown\n{row}\n~~~", "  ```text\n{row}\n  ```", "<!--\n{row}\n-->", "<!-- {row} -->",
])
def test_cr2_rows_in_fences_or_comments_do_not_count(wrap):
    row = f"| {REF} | 2026-10-12 | PO approved HW gate | approved (PO) |"
    assert hl.approval_row("Example:\n" + wrap.format(row=row) + "\nend\n", REF) is None
    assert hl.approval_row(wrap.format(row="x") + "\n" + row + "\n", REF) == row      # a real row after a fence counts


def test_cr2_check_approval_with_fenced_example(tmp_path):
    import datetime as dt
    (tmp_path / "00_System" / "specs").mkdir(parents=True)
    (tmp_path / "00_System" / "specs" / "DECISIONS.md").write_text(
        f"Template:\n```\n| {REF} | 2026-10-12 | PO approved HW gate | approved (PO) |\n```\n", encoding="utf-8")
    (tmp_path / "00_System" / "STATUS.md").write_text("status", encoding="utf-8")
    with pytest.raises(hl.InterlockError):
        hl.check_approval(REF, today=dt.date(2026, 10, 12), repo=tmp_path)


# C-R3: an exception in an identity step (or before the device is identified) sends nothing to the device
class RecLink(SimpleNamespace):
    def __init__(self, **kw):
        super().__init__(is_twin=False, sent=[], **kw)

    def cmd(self, c, *a, **k):
        self.sent.append(c)
        return {"status": "OK"}


@pytest.mark.parametrize("item, identified", [("S-00", False), ("S-ID", False), ("IMG-REL", True), ("HG-28", False)])
def test_cr3_no_halt_clear_to_unidentified_device(tmp_path, item, identified):
    import hil_procs as P
    link = RecLink()
    ctx = _ctx(tmp_path, link=link)
    ctx.identified = identified

    def boom(_ctx):
        raise TimeoutError("GET_INFO: no response (wrong device?)")
    it = P.run_item(ctx, item, "x", boom)
    assert it.error.startswith("TimeoutError") and link.sent == []


def test_cr3_identified_device_still_gets_holding_stop(tmp_path):
    import hil_procs as P
    link = RecLink()
    ctx = _ctx(tmp_path, link=link)
    ctx.identified = True

    def boom(_ctx):
        raise RuntimeError("procedure failed")
    P.run_item(ctx, "HG-28", "x", boom)
    assert link.sent[0] == "HALT"


def test_cr3_identity_step_error_stops_session():
    import hil_session as S
    assert S.stop_reason("S-00", SimpleNamespace(error="TimeoutError: x", verdict="ERROR"))
    assert S.stop_reason("IMG-REL", SimpleNamespace(error="REFUSED: wrong image", verdict="ERROR"))


# O-2: a FAIL / INCONCLUSIVE in an identity or bench-entry step stops the session before any motion
@pytest.mark.parametrize("item, verdict, stop", [
    ("S-00", "FAIL", True), ("IMG-DWT", "INCONCLUSIVE", True), ("BENCH-ENTRY", "FAIL", True),
    ("S-00", "PARTIAL (MANUAL open)", False), ("BENCH-ENTRY", "PARTIAL (MANUAL, TARGET-ONLY open)", False),
    ("HG-28", "FAIL", False),
])
def test_o2_fail_in_identity_or_bench_entry_stops(item, verdict, stop):
    import hil_session as S
    assert bool(S.stop_reason(item, SimpleNamespace(error="", verdict=verdict))) == stop


def test_o2_dip_not_applied_refuses(tmp_path):
    import hil_procs as P
    import hil_session as S
    from hil_operator import Answer  # noqa: F401  (type only)

    class Op(FakeOp):
        def ask_text(self, key, text, nominal=""):
            return SimpleNamespace(value="x", evidence=True, source="operator")

        def ask_yes_no(self, key, text, twin=None, nominal=True):
            return SimpleNamespace(value=False, evidence=True, source="operator")
    link = SimpleNamespace(is_twin=False, info=lambda: _info())
    ctx = _ctx(tmp_path, link=link)
    ctx.op = Op()
    ctx.begin("S-00", "start")
    with pytest.raises(P.BenchRefused):
        S.s_start(ctx)


# O-1: suffix and FEAT_HW_MEAS must agree; an inconsistent build is refused, never taken for the requested image
@pytest.mark.parametrize("build, feats, kind", [
    ("261012-0900", ["HW_MEAS"], "unknown"), ("261012-0900-MEAS", [], "unknown"), ("261012-0900-DWT", [], "unknown"),
    ("261012-0900", [], "release"), ("261012-0900-MEAS", ["HW_MEAS"], "meas"), ("261012-0900-DWT", ["HW_MEAS"], "meas_dwt"),
])
def test_o1_image_kind_from_suffix_and_feature(build, feats, kind):
    import hil_procs as P
    assert P.image_name_of({"build": build, "features": feats}) == kind


def test_o1_release_requested_but_hw_meas_build_refused(tmp_path):
    import hil_procs as P
    inf = _info(build="261012-0900", features=["AFE", "MOTION", "HW_MEAS"])
    link = SimpleNamespace(is_twin=False, info=lambda: inf)
    ctx = _ctx(tmp_path, link=link)
    ctx.begin("IMG-REL", "release")
    with pytest.raises(P.BenchRefused):
        P.verify_image(ctx, "release")
    assert not ctx.identified


# O-3: --only / --from ids are validated before any port is opened
@pytest.mark.parametrize("sel", [["--only", "HG-99"], ["--from", "HG-99"], ["--only", "HG-11", "--from", "HG-12"]])
def test_o3_selection_validated_before_port(monkeypatch, capsys, tmp_path, sel):
    import hil_session as S
    built = []
    monkeypatch.setattr(S, "SerialTransport", lambda *a, **k: built.append(a))
    rc = S.main(["--port", "COM7", "--approved", REF, "--board-uid", UID, "--out", str(tmp_path / "s")] + sel)
    assert rc == 2 and built == [] and "REFUSED" in capsys.readouterr().err


# C-R4 (Integrator re-review §9.2): a row in an indented code block is an example, not a record
@pytest.mark.parametrize("indent", ["    ", "\t", "  \t", "        "])
def test_indented_code_block_row_counts(indent):
    row = f"| {REF} | 2026-10-12 | PO approved HW gate | approved (PO) |"
    text = f"Example of a record:\n\n{indent}{row}\n\nend\n"
    assert hl.approval_row(text, REF) is None
    assert hl.approval_row(f"  {row}\n", REF) is not None          # 1-3 spaces: still a normal table row


def test_cr4_check_approval_with_indented_example(tmp_path):
    import datetime as dt
    (tmp_path / "00_System" / "specs").mkdir(parents=True)
    (tmp_path / "00_System" / "specs" / "DECISIONS.md").write_text(
        f"Template:\n\n    | {REF} | 2026-10-12 | PO approved HW gate | approved (PO) |\n", encoding="utf-8")
    (tmp_path / "00_System" / "STATUS.md").write_text("status", encoding="utf-8")
    with pytest.raises(hl.InterlockError):
        hl.check_approval(REF, today=dt.date(2026, 10, 12), repo=tmp_path)


# HG-18 (D-51, re-check of FW v0.8): NFR-007 verdict sections and criteria
def test_hg18_measures_nfr007_sections_with_entry_exit():
    import hil_budget as hb
    import hil_procs as P
    assert P.DWT_N == 24 and {1, 2, 3, 4, 23} <= set(P.DWT_BUDGETS)
    assert P.DWT_BUDGETS[1] == 2.0 and P.DWT_BUDGETS[2] == 1.0                     # NFR-007
    assert all(P.DWT_BUDGETS[s] == 2.5 for s in (3, 4, 5, 23))                       # D-52 level-1 handlers
    assert all(P.DWT_BUDGETS[s] == 1.0 for s in (11, 12, 13, 16, 17, 18))            # critical sections
    assert {1, 2, 3, 4, 23} <= set(P.DWT_ISR) and P.EXC_CYCLES == 27.0
    ovh = (11.0, 2.0)
    sec = hb.DwtSection(2, True, 10, 100, 180 - 27 + 11, 1500, [10] + [0] * 9)   # body 153 cyc net
    assert hb.check_dwt("estop", sec, 1.0, ovh).decision == hb.PASS                # body only: 0.85 µs
    c = hb.check_dwt("estop", sec, 1.0, ovh, exc_cycles=P.EXC_CYCLES)              # total 180 cyc = 1.0 µs
    assert c.decision in (hb.PASS, hb.INCONCL) and "entry/exit" in c.criterion
    sec2 = hb.DwtSection(2, True, 10, 100, 200, 1500, [10] + [0] * 9)
    assert hb.check_dwt("estop", sec2, 1.0, ovh, exc_cycles=P.EXC_CYCLES).decision == hb.FAIL


def test_hg18_d52_step_plus_largest_level1():
    import hil_budget as hb
    import hil_procs as P
    ovh = (11.0, 2.0)

    def sec(n, mx):
        return hb.DwtSection(n, True, 5, 50, mx, 5 * mx, [5] + [0] * 9)
    secs = {1: sec(1, 11 + 360 - 27), 3: sec(3, 11 + 450 - 27), 4: sec(4, 100), 5: sec(5, 100), 23: sec(23, 100)}
    c = P.step_plus_level1(secs, ovh)                         # 2.0 + 2.5 = 4.5 µs <= 5 µs
    assert c.decision == hb.PASS and "section 3" in c.note
    secs[23] = sec(23, 11 + 560 - 27)                          # 2.0 + 3.11 = 5.11 µs > 5 µs
    c = P.step_plus_level1(secs, ovh)
    assert c.decision == hb.FAIL and "section 23" in c.note
    assert P.step_plus_level1({1: sec(1, 300)}, ovh).decision == hb.NOT_MEASURED

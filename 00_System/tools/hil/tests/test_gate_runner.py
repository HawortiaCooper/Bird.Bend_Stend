"""Validator E - gate runner (D-57): approval file (spec GATE_RUNNER.md §3), make_approval, the session CLI without the
repository, the results zip and its manifest, the offline analysis, a twin rehearsal through the runner, and the
standing data rule (a run writes nothing outside its own output folder). No hardware, no serial port (D-06).

    .venv\\Scripts\\python -m pytest 00_System\\tools\\hil\\tests\\test_gate_runner.py -q

Verifies: SYS-009, D-06 (interlock), D-57 (b) approval file, (c) results zip / offline analysis
TC: TC-SYS-009-04
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

HIL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIL))

import gate_approval as ga  # noqa: E402
import gate_export  # noqa: E402
import gate_res  # noqa: E402
import hil_link as hl  # noqa: E402
import make_approval as ma  # noqa: E402

UID = "3400451A3133510237363934"
DAY = dt.date(2026, 10, 12)
REF = f"D-06-GATE-{DAY:%Y%m%d}"
RUNNER_SHA = gate_res.runner_identity()["sha256"]


# ------------------------------------------------------------------------------------------- fixtures
def make_repo(root: Path, ref: str, extra_rows: str = "") -> Path:
    (root / "00_System" / "specs").mkdir(parents=True, exist_ok=True)
    (root / "00_System" / "specs" / "DECISIONS.md").write_text(
        f"| {ref} | {ref[10:14]}-{ref[14:16]}-{ref[16:18]} | PO approved HW gate: board UID {UID}, COM7, §6.8 "
        "| approved (PO) |\n"
        "| RR-HG-12-01 | 2026-10-12 | HG-12 load-limit timing accepted open (§6.6 R-4) | accepted (PO) |\n"
        "| RR-HG-25-01 | 2026-10-12 | HG-25 speed envelope accepted open | proposed |\n" + extra_rows,
        encoding="utf-8")
    (root / "00_System" / "STATUS.md").write_text("status", encoding="utf-8")
    return root


def make_delivery(root: Path) -> Path:
    d = root / "04_Delivery"
    bu, gk = d / "BringUp_FW9.9" / "firmware", d / "GateKit_FW9.9"
    bu.mkdir(parents=True)
    gk.mkdir(parents=True)
    files = {"release": bu / "BBS_FW_9.9_release.hex", "meas": gk / "BBS_FW_9.9_HW_MEAS_GATE-ONLY.hex",
             "meas_dwt": gk / "BBS_FW_9.9_HW_MEAS_DWT_GATE-ONLY.hex"}
    for k, f in files.items():
        f.write_text(f":020000040800F2\n:00000001FF\n; {k}\n", encoding="ascii")
    head = (f"Git commit                        : 0123456789abcdef0123456789abcdef01234567\n"
            f"GET_INFO fw_version (wire)        : 9.9.0\n"
            f"PARAM_DICT_HASH / version / count : {hl.DICT_HASH} / 6 / 48\n")
    (bu / "BUILD_INFO.txt").write_text(head + "[release]  env nucleo_f446re\n  GET_INFO build     : 20261012-0900   (git x)\n",
                                       encoding="utf-8")
    (gk / "BUILD_INFO.txt").write_text(
        head + "[HW_MEAS_GATE-ONLY]  env nucleo_f446re_meas\n  GET_INFO build     : 261012-0900-MEAS\n"
        "[HW_MEAS_DWT_GATE-ONLY]  env nucleo_f446re_meas_dwt\n  GET_INFO build     : 261012-0900-DWT\n"
        "TC-SYS-009-02 check_meas_build.py PASS (measurement images: …)\n", encoding="utf-8")
    (bu / "SHA256SUMS.txt").write_text(f"{gate_res.sha256_file(files['release'])} *{files['release'].name}\n")
    (gk / "SHA256SUMS.txt").write_text("".join(f"{gate_res.sha256_file(files[k])} *{files[k].name}\n"
                                               for k in ("meas", "meas_dwt")))
    return d


@pytest.fixture
def env(tmp_path):
    repo = make_repo(tmp_path / "repo", REF)
    delivery = make_delivery(tmp_path)
    doc = ma.build(REF, UID, delivery, RUNNER_SHA, ["HG-12:RR-HG-12-01"], 7, today=DAY, repo=repo)
    f = tmp_path / "approval.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    return {"repo": repo, "delivery": delivery, "file": f, "doc": doc, "tmp": tmp_path}


def write(tmp: Path, doc: dict, reseal: bool = True) -> Path:
    f = tmp / "approval_mod.json"
    f.write_text(json.dumps(ga.seal(doc) if reseal else doc), encoding="utf-8")
    return f


def check(env, f=None, **kw):
    kw.setdefault("today", DAY + dt.timedelta(days=1))
    kw.setdefault("delivery", env["delivery"])
    return ga.check(f or env["file"], **kw)


# ------------------------------------------------------------------------------------------- approval file
def test_valid_approval_file(env):
    a = check(env, board_uid=UID.lower())
    assert a.reference == REF and a.board_uid == UID and a.accepted_open == {"HG-12": "RR-HG-12-01"}
    assert a.builds() == {"release": "20261012-0900", "meas": "261012-0900-MEAS", "meas_dwt": "261012-0900-DWT"}
    assert a.evidence["tc_sys_009_02"].startswith("PASS")
    assert a.valid_until == DAY + dt.timedelta(days=7)


def test_refuses_missing_file(env):
    with pytest.raises(hl.InterlockError, match="not found"):
        check(env, env["tmp"] / "nope.json")


def test_refuses_tampered_file(env):
    doc = dict(env["doc"], board_uid="3400451A3133510237363999")     # edited without the Orchestrator's tool
    with pytest.raises(hl.InterlockError, match="body_sha256"):
        check(env, write(env["tmp"], doc, reseal=False))


@pytest.mark.parametrize("today, msg", [(DAY + dt.timedelta(days=8), "expired"), (DAY - dt.timedelta(days=1), "valid from")])
def test_refuses_outside_validity(env, today, msg):
    with pytest.raises(hl.InterlockError, match=msg):
        check(env, today=today)


def test_refuses_wrong_board_uid(env):
    with pytest.raises(hl.InterlockError, match="wrong board"):
        check(env, board_uid="3400451A3133510237363935")


def test_refuses_wrong_runner_hash(env):
    with pytest.raises(hl.InterlockError, match="runner hash"):
        check(env, runner_sha="0" * 64)


def test_refuses_changed_kit_image(env):
    hexf = env["delivery"] / env["doc"]["kit"]["images"]["meas"]["path"]
    hexf.write_text(hexf.read_text() + "; changed\n")
    with pytest.raises(hl.InterlockError, match="SHA-256"):
        check(env)


def test_refuses_missing_kit_image(env):
    (env["delivery"] / env["doc"]["kit"]["images"]["release"]["path"]).unlink()
    with pytest.raises(hl.InterlockError, match="not found"):
        check(env)


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d.update(valid_until=(DAY + dt.timedelta(days=8)).isoformat()), "window"),
    (lambda d: d.update(valid_from=(DAY + dt.timedelta(days=1)).isoformat()), "reference date"),
    (lambda d: d.update(reference="D-06-GATE-2026101"), "D-06-GATE"),
    (lambda d: d.update(decision_row=d["decision_row"].replace(UID, "board")), "decision_row"),
    (lambda d: d.update(decision_row=d["decision_row"].replace("approved (PO) |", "pending |")), "decision_row"),
    (lambda d: d["kit"].update(param_dict_hash="0x00000000"), "dictionary hash"),
    (lambda d: d["kit"]["images"]["meas"].update(path="GateKit_FW9.9/x.bin"), ".hex"),
    (lambda d: d["kit"]["images"].pop("meas_dwt"), "exactly"),
    (lambda d: d["accepted_open"].append({"item": "HG-25", "ref": "RR-HG-25-01",
                                          "row": "| RR-HG-25-01 | x | HG-25 accepted open | proposed |"}), "accepted"),
    (lambda d: d.update(schema="other"), "schema"),
    (lambda d: d.pop("runner"), "runner"),
])
def test_refuses_inconsistent_content(env, mutate, msg):
    doc = json.loads(json.dumps(env["doc"]))
    mutate(doc)
    with pytest.raises(hl.InterlockError, match=msg):
        check(env, write(env["tmp"], doc))


# ------------------------------------------------------------------------------------------- make_approval
@pytest.mark.parametrize("rows, today, msg", [
    ("", DAY + dt.timedelta(days=8), "older than"),                                       # stale reference
    (f"| {REF} | {DAY} | approval revoked by the PO | revoked |\n", DAY, "not recorded"),   # C-R1 revocation
])
def test_make_approval_refuses_bad_record(tmp_path, rows, today, msg):
    repo = make_repo(tmp_path / "repo", REF, rows)
    with pytest.raises(hl.InterlockError, match=msg):
        ma.build(REF, UID, make_delivery(tmp_path), RUNNER_SHA, [], 7, today=today, repo=repo)


def test_make_approval_needs_uid_in_row_and_recorded_open_items(tmp_path):
    repo = make_repo(tmp_path / "repo", REF)
    d = make_delivery(tmp_path)
    with pytest.raises(hl.InterlockError, match="does not name the board UID"):
        ma.build(REF, "3400451A3133510237363935", d, RUNNER_SHA, [], 7, today=DAY, repo=repo)
    with pytest.raises(hl.InterlockError):
        ma.build(REF, UID, d, RUNNER_SHA, ["HG-25:RR-HG-25-01"], 7, today=DAY, repo=repo)


def test_make_approval_refuses_hash_not_in_sha256sums(tmp_path):
    repo = make_repo(tmp_path / "repo", REF)
    d = make_delivery(tmp_path)
    f = next((d / "GateKit_FW9.9").glob("*_HW_MEAS_GATE-ONLY.hex"))
    f.write_text(f.read_text() + "; rebuilt\n")
    with pytest.raises(hl.InterlockError, match="SHA256SUMS"):
        ma.build(REF, UID, d, RUNNER_SHA, [], 7, today=DAY, repo=repo)


# ------------------------------------------------------------------------------------------- session CLI
@pytest.fixture
def today_env(tmp_path):
    today = dt.date.today()
    ref = f"D-06-GATE-{today:%Y%m%d}"
    repo = make_repo(tmp_path / "repo", ref)
    delivery = make_delivery(tmp_path)
    doc = ma.build(ref, UID, delivery, RUNNER_SHA, [], 7, today=today, repo=repo)
    f = tmp_path / "approval.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    return {"file": f, "delivery": delivery, "tmp": tmp_path, "doc": doc}


def _no_serial(monkeypatch):
    import serial
    opened = []
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: opened.append((a, k)))
    return opened


@pytest.mark.parametrize("extra", [
    [],                                                        # no approval file
    ["--approved", "D-06-GATE-20261012"],                      # the old repository reference is gone (D-57)
    ["--approval", "missing.json"],
    ["--approval", "APPROVAL", "--accepted-open", "HG-12:RR-HG-12-01"],     # accepted-open only from the file
    ["--approval", "APPROVAL", "--board-uid", "3400451A3133510237363935"],  # wrong board
    ["--approval", "APPROVAL", "--board-uid", "not-a-uid"],
])
def test_cli_refuses_before_any_port(monkeypatch, capsys, today_env, extra):
    import hil_session
    opened = _no_serial(monkeypatch)
    extra = [str(today_env["file"]) if x == "APPROVAL" else x for x in extra]
    rc = hil_session.main(["--port", "COM7", "--delivery", str(today_env["delivery"]),
                           "--out", str(today_env["tmp"] / "s")] + extra)
    assert rc == 2 and opened == [] and "REFUSED" in capsys.readouterr().err
    assert not (today_env["tmp"] / "s").exists()                 # refused before the session folder is created


def test_cli_refuses_wrong_kit_before_any_port(monkeypatch, capsys, today_env):
    import hil_session
    opened = _no_serial(monkeypatch)
    hexf = today_env["delivery"] / today_env["doc"]["kit"]["images"]["release"]["path"]
    hexf.write_text("tampered")
    rc = hil_session.main(["--port", "COM7", "--approval", str(today_env["file"]), "--delivery",
                           str(today_env["delivery"]), "--out", str(today_env["tmp"] / "s")])
    assert rc == 2 and opened == [] and "SHA-256" in capsys.readouterr().err


def test_cli_valid_approval_reaches_the_interlocked_port(monkeypatch, capsys, today_env):
    """A valid file passes every check; the port is then requested through SerialTransport with the validated
    Approval (here a fake that refuses), the operator still re-types the port (open_serial)."""
    import hil_session
    seen = []

    def fake(port, approval, confirm):
        seen.append((port, approval))
        raise hl.InterlockError("FAKE: no port in tests (D-06)")
    monkeypatch.setattr(hil_session, "SerialTransport", fake)
    rc = hil_session.main(["--port", "COM7", "--approval", str(today_env["file"]), "--delivery",
                           str(today_env["delivery"]), "--out", str(today_env["tmp"] / "s")])
    assert rc == 2 and "FAKE" in capsys.readouterr().err
    assert seen and seen[0][0] == "COM7" and seen[0][1].board_uid == UID
    out = today_env["tmp"] / "s"
    assert json.loads((out / "approval.json").read_text())["body_sha256"] == today_env["doc"]["body_sha256"]
    assert json.loads((out / "runner.json").read_text())["runner"]["sha256"] == RUNNER_SHA


def test_open_serial_rechecks_window_and_refuses_strings_when_frozen(monkeypatch, env):
    a = check(env)
    calls = []
    op = lambda **k: calls.append(k)                          # noqa: E731
    with pytest.raises(hl.InterlockError, match="expired"):
        hl.open_serial("COM7", a, today=DAY + dt.timedelta(days=9), confirm=lambda p: True, opener=op)
    hl.open_serial("COM7", a, today=DAY + dt.timedelta(days=2), confirm=lambda p: True, opener=op)
    assert len(calls) == 1
    monkeypatch.setattr(gate_res, "FROZEN", True)
    with pytest.raises(hl.InterlockError, match="approval file"):
        hl.open_serial("COM7", REF, today=DAY, confirm=lambda p: True, opener=op)


# ------------------------------------------------------------------------------------------- results zip
def _session_dir(root: Path) -> Path:
    out = root / "sess"
    (out / "results").mkdir(parents=True)
    (out / "raw").mkdir()
    (out / "session.json").write_text(json.dumps({"session_id": "abcdef0123", "mode": "twin", "date": "2026-10-12 10:00"}))
    (out / "runner.json").write_text(json.dumps({"runner": gate_res.runner_identity()}))
    (out / "results" / "HG-09.json").write_text(json.dumps({"id": "HG-09", "verdict": "PASS", "checks": [],
                                                                "session": {"session_id": "abcdef0123"}}))
    (out / "raw" / "frames.bin").write_bytes(hl.RAW_MAGIC)
    (out / "prompts.jsonl").write_text("")
    (out / "twin_run").mkdir()
    (out / "twin_run" / "big.log").write_text("not exported")
    return out


def test_zip_manifest_verifies_and_detects_changes(tmp_path):
    out = _session_dir(tmp_path)
    z = gate_export.export(out)
    assert z.parent == out and gate_export.verify(z) == []
    with zipfile.ZipFile(z) as zz:
        names = set(zz.namelist())
    assert "twin_run/big.log" not in names and {"manifest.json", "results/HG-09.json", "raw/frames.bin"} <= names
    # tamper: change a member, add one, drop one
    for how in ("change", "add", "drop"):
        bad = tmp_path / f"bad_{how}.zip"
        with zipfile.ZipFile(z) as src, zipfile.ZipFile(bad, "w") as dst:
            for n in src.namelist():
                b = src.read(n)
                if how == "drop" and n == "results/HG-09.json":
                    continue
                if how == "change" and n == "results/HG-09.json":
                    b = b.replace(b"PASS", b"FAIL")
                dst.writestr(n, b)
            if how == "add":
                dst.writestr("results/HG-10a.json", b"{}")
        assert gate_export.verify(bad), how


# ------------------------------------------------------------------------------------------- twin rehearsal
@pytest.fixture(scope="module")
def twin_exe(tmp_path_factory):
    sys.path.insert(0, str(HIL.parent / "fw_twin"))
    import build as twin_build  # noqa: PLC0415
    d = tmp_path_factory.mktemp("twin_build")
    return twin_build.ensure_built("fw", d)


def test_twin_rehearsal_through_the_runner(tmp_path, twin_exe):
    import gate_runner
    import hil_analyze
    out = tmp_path / "rehearsal"
    rc = gate_runner.main(["--twin", "--quick", "--only", "HG-09", "--twin-exe", str(twin_exe), "--out", str(out)])
    assert rc == 0
    zips = list(out.glob("gate_*_twin_*.zip"))
    assert len(zips) == 1 and gate_export.verify(zips[0]) == []
    assert gate_runner.main(["--verify-zip", str(zips[0])]) == 0
    rep = hil_analyze.analyse(zips[0])
    assert rep["problems"] == [] and rep["mismatches"] == []
    assert rep["raw"]["frames_pc_to_fw"] > 0 and rep["raw"]["crc_errors"] == 0
    assert {i["id"] for i in rep["items"]} == {"S-00", "HG-09"}


def test_stray_run_writes_only_into_its_output_folder(tmp_path, twin_exe):
    """Standing data rule: a runner run (twin rehearsal, subprocess, no bytecode) leaves the working directory and the
    user / temp folders untouched; every file it creates is below --out."""
    root = tmp_path / "stray"
    cwd, out = root / "cwd", root / "out"
    homes = {k: root / "env" / k for k in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")}
    for p in (cwd, *homes.values()):
        p.mkdir(parents=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **{k: str(v) for k, v in homes.items()})
    env.pop("BEND_TWIN_BUILD_DIR", None)
    r = subprocess.run([sys.executable, "-B", str(HIL / "gate_runner.py"), "--twin", "--quick", "--only", "HG-09",
                        "--twin-exe", str(twin_exe), "--out", str(out)], cwd=cwd, env=env, capture_output=True,
                       text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-2000:]
    written = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    stray = [p for p in written if not p.startswith("out/")]
    assert stray == [], stray
    assert any(p.startswith("out/gate_") and p.endswith(".zip") for p in written)


# ------------------------------------------------------------------------------------------- identity / analysis
def test_frozen_bundle_hash_pins_exe_and_bundled_data(tmp_path):
    exe = tmp_path / "BirdBendStand-GateRunner.exe"
    exe.write_bytes(b"MZ runner")
    data = tmp_path / "_internal"
    (data / "resources" / "pictures").mkdir(parents=True)
    (data / "resources" / "params.yaml").write_text("a: 1")
    (data / "resources" / "pictures" / "B1.png").write_bytes(b"png")
    assert gate_res.bundle_root_of(exe) == data
    h0 = gate_res.bundle_sha256(exe, data)
    (data / "resources" / "params.yaml").write_text("a: 2")          # a changed dictionary in the bundle
    h1 = gate_res.bundle_sha256(exe, data)
    (data / "resources" / "pictures" / "B1.png").write_bytes(b"png2")
    h2 = gate_res.bundle_sha256(exe, data)
    assert len({h0, h1, h2}) == 3


def test_analysis_flags_a_doctored_decision(tmp_path):
    import hil_analyze
    out = _session_dir(tmp_path)
    chk = {"name": "a last PUL", "criterion": "max + u <= 100 µs", "decision": "PASS", "value": 120.0, "unit": "µs",
           "u": 1.0, "budget": 100.0, "n": 3, "note": "", "stats": {"max": 120.0}}
    (out / "results" / "HG-10a.json").write_text(json.dumps(
        {"id": "HG-10a", "verdict": "PASS", "checks": [chk], "session": {"session_id": "abcdef0123"}}))
    rep = hil_analyze.analyse(gate_export.export(out))
    assert any("HG-10a" in m and "re-evaluated FAIL" in m for m in rep["mismatches"])


# ------------------------------------------------------------------------------------------- GR-5 approval log
def test_log_row_is_never_an_approval_record(tmp_path):
    """GATE_APPROVAL_LOG.md is not scanned by the approval-row parser (only DECISIONS.md / STATUS.md), and its rows
    start with the issue time, so even a log in a scanned file would not be a record for the reference (C-R1)."""
    repo = tmp_path / "repo"
    (repo / "00_System" / "specs").mkdir(parents=True)
    (repo / "00_System" / "specs" / "DECISIONS.md").write_text("no approval here\n", encoding="utf-8")
    (repo / "00_System" / "STATUS.md").write_text("status\n", encoding="utf-8")
    (repo / "00_System" / "specs" / "GATE_APPROVAL_LOG.md").write_text(
        f"| {REF} | 2026-10-12 | PO approved HW gate, board UID {UID} | approved (PO) |\n", encoding="utf-8")
    assert "00_System/specs/GATE_APPROVAL_LOG.md" not in hl.APPROVAL_FILES
    with pytest.raises(hl.InterlockError):
        hl.check_approval(REF, today=DAY, repo=repo)
    row = f"| 2026-10-12T09:00:00+00:00 | {'a' * 64} | {REF} | {UID} | abc | {'b' * 64} | HG1 |"
    assert hl.approval_row(row, REF) is None


def test_make_approval_prints_log_row_and_analysis_uses_the_log(env, tmp_path):
    import hil_analyze
    doc = env["doc"]
    row = ma.log_row(doc, "HG1 session | operator")
    cells = hl._cells(row)
    assert cells[0] == doc["generated_by"]["utc"] and cells[1] == doc["body_sha256"] and cells[2] == REF
    assert cells[3] == UID and cells[4] == doc["kit"]["commit"] and cells[5] == RUNNER_SHA and "|" not in cells[6]
    log_text = ("| Issued (UTC) | Approval body SHA-256 | D-06-GATE ref | Board UID | Kit commit | Runner hash | "
                "Issued for |\n|---|---|---|---|---|---|---|\n" + row + "\n")
    rows = hil_analyze.parse_approval_log(log_text)
    assert len(rows) == 1 and rows[0]["body_sha256"] == doc["body_sha256"]
    # a session zip that ran under this approval file
    out = _session_dir(tmp_path)
    (out / "approval.json").write_text(json.dumps(doc), encoding="utf-8")
    meta = json.loads((out / "session.json").read_text())
    meta.update(mode="target", board_uid=UID, approval=REF, approval_sha256=doc["body_sha256"],
                options=dict(ga.DEFAULT_OPTIONS))
    (out / "session.json").write_text(json.dumps(meta))
    (out / "runner.json").write_text(json.dumps({"runner": {"sha256": RUNNER_SHA}}))
    (out / "prompts.jsonl").write_text(json.dumps({"source": "operator"}) + "\n")
    (out / "results" / "HG-09.json").write_text(json.dumps(
        {"id": "HG-09", "verdict": "PASS", "checks": [], "session": {"session_id": "abcdef0123", "board_uid": UID}}))
    z = gate_export.export(out)
    zsha = gate_res.sha256_file(z)
    rep = hil_analyze.analyse(z, zip_sha=zsha, approval_log=rows)
    hil_analyze.check_against_log(rep, rows)
    assert rep["problems"] == [] and rep["approval"]["log_row"] == doc["generated_by"]["utc"]
    rep2 = hil_analyze.analyse(z)
    hil_analyze.check_against_log(rep2, hil_analyze.parse_approval_log(log_text.replace(doc["body_sha256"], "c" * 64)))
    assert any("not in the approval log" in p for p in rep2["problems"])
    rep3 = hil_analyze.analyse(z)
    hil_analyze.check_against_log(rep3, hil_analyze.parse_approval_log(log_text.replace(UID, "3400451A3133510237363999")))
    assert any("board_uid" in p for p in rep3["problems"])


# ------------------------------------------------------------------------------------------- C-GR-1 options
@pytest.mark.parametrize("extra", [["--quick"], ["--seed", "7"], ["--counts-per-n", "3000"], ["--buffer-fitted"],
                                   ["--power-sense-fitted"]])
def test_board_session_refuses_unpinned_options(monkeypatch, capsys, today_env, extra):
    import hil_session
    opened = _no_serial(monkeypatch)
    rc = hil_session.main(["--port", "COM7", "--approval", str(today_env["file"]), "--delivery",
                           str(today_env["delivery"]), "--out", str(today_env["tmp"] / "s")] + extra)
    assert rc == 2 and opened == [] and "not pinned" in capsys.readouterr().err


def _pinned_env(tmp_path, options):
    today = dt.date.today()
    ref = f"D-06-GATE-{today:%Y%m%d}"
    repo = make_repo(tmp_path / "repo", ref)
    delivery = make_delivery(tmp_path)
    doc = ma.build(ref, UID, delivery, RUNNER_SHA, [], 7, today=today, repo=repo, options=options)
    f = tmp_path / "approval.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    return f, delivery, doc


def test_pinned_option_is_accepted_and_applied(monkeypatch, capsys, tmp_path):
    import hil_session
    f, delivery, doc = _pinned_env(tmp_path, ["buffer_fitted=true", "counts_per_n=3100"])
    assert doc["options"] == {"buffer_fitted": True, "counts_per_n": 3100.0}
    seen = []

    def fake(port, approval, confirm):
        seen.append(approval.options)
        raise hl.InterlockError("FAKE: no port in tests (D-06)")
    monkeypatch.setattr(hil_session, "SerialTransport", fake)
    out = tmp_path / "s"
    rc = hil_session.main(["--port", "COM7", "--approval", str(f), "--delivery", str(delivery), "--out", str(out),
                           "--buffer-fitted"])                       # typed and pinned with the same value: allowed
    assert rc == 2 and "FAKE" in capsys.readouterr().err and seen == [{"buffer_fitted": True, "counts_per_n": 3100.0}]
    with pytest.raises(hl.InterlockError):
        ga.parse_option("quick=maybe")
    with pytest.raises(hl.InterlockError):
        ga.parse_option("speed=3")


def test_approval_with_unknown_option_is_refused(env):
    doc = json.loads(json.dumps(env["doc"]))
    doc["options"] = {"fast": True}
    with pytest.raises(hl.InterlockError, match="options"):
        check(env, write(env["tmp"], doc))


# ------------------------------------------------------------------------------------------- C-GR-2 / anchors
def _target_zip(tmp_path, env_doc, options=None, pinned=(), quick=False):
    out = _session_dir(tmp_path)
    (out / "approval.json").write_text(json.dumps(env_doc), encoding="utf-8")
    meta = json.loads((out / "session.json").read_text())
    meta.update(mode="target", board_uid=UID, approval=REF, approval_sha256=env_doc["body_sha256"], quick=quick,
                options=dict(ga.DEFAULT_OPTIONS, **(options or {})), options_pinned=list(pinned))
    (out / "session.json").write_text(json.dumps(meta))
    (out / "runner.json").write_text(json.dumps({"runner": {"sha256": RUNNER_SHA}}))
    (out / "prompts.jsonl").write_text(json.dumps({"source": "operator"}) + "\n")
    (out / "results" / "HG-09.json").write_text(json.dumps(
        {"id": "HG-09", "verdict": "PASS", "checks": [], "session": {"session_id": "abcdef0123", "board_uid": UID}}))
    return gate_export.export(out)


def test_analysis_needs_both_anchors_for_a_board_zip(env, tmp_path):
    import hil_analyze
    z = _target_zip(tmp_path, env["doc"])
    rep = hil_analyze.analyse(z)
    assert any("no approval anchor" in p for p in rep["problems"])
    assert any("results-zip anchor" in p for p in rep["problems"])
    good = gate_res.sha256_file(z)
    rep = hil_analyze.analyse(z, approval_sha=env["doc"]["body_sha256"], zip_sha=good)
    assert rep["problems"] == [], rep["problems"]
    rep = hil_analyze.analyse(z, approval_sha=env["doc"]["body_sha256"], zip_sha="0" * 64)
    assert any("changed after the session" in p for p in rep["problems"])
    assert hil_analyze.main([str(z), "--approval-sha", env["doc"]["body_sha256"], "--zip-sha",
                             f"{z.name}   SHA-256   {good}", "--out", str(tmp_path / "d.md")]) == 0
    assert hil_analyze.main([str(z), "--out", str(tmp_path / "d2.md")]) == 1        # no anchors: never 'consistent'


@pytest.mark.parametrize("kw, msg", [({"quick": True}, "--quick"),
                                     ({"options": {"buffer_fitted": True}}, "not pinned")])
def test_analysis_flags_quick_and_unpinned_options(env, tmp_path, kw, msg):
    import hil_analyze
    z = _target_zip(tmp_path, env["doc"], **kw)
    rep = hil_analyze.analyse(z, approval_sha=env["doc"]["body_sha256"], zip_sha=gate_res.sha256_file(z))
    assert any(msg in p for p in rep["problems"])


def test_analysis_accepts_a_pinned_option_as_a_note(env, tmp_path):
    import hil_analyze
    z = _target_zip(tmp_path, env["doc"], options={"buffer_fitted": True}, pinned=["buffer_fitted"])
    rep = hil_analyze.analyse(z, approval_sha=env["doc"]["body_sha256"], zip_sha=gate_res.sha256_file(z))
    assert rep["problems"] == [] and any("pinned" in n for n in rep["notes"])


# ------------------------------------------------------------------------------------------- C-GR-3 bundle hash
def test_bundle_hash_covers_the_whole_bundle_root(tmp_path):
    exe = tmp_path / "BirdBendStand-GateRunner.exe"
    exe.write_bytes(b"MZ runner")
    data = tmp_path / "_internal"
    (data / "PySide").mkdir(parents=True)
    (data / "base_library.zip").write_bytes(b"zip")
    (data / "python314.dll").write_bytes(b"dll")
    h0 = gate_res.bundle_sha256(exe, gate_res.bundle_root_of(exe))
    (data / "base_library.zip").write_bytes(b"zip patched")            # code outside resources/ is pinned too
    h1 = gate_res.bundle_sha256(exe, data)
    (data / "PySide" / "extra.pyd").write_bytes(b"new")                # an added file is pinned too
    h2 = gate_res.bundle_sha256(exe, data)
    assert len({h0, h1, h2}) == 3
    with pytest.raises(FileNotFoundError):
        gate_res.bundle_root_of(tmp_path / "x" / "onefile.exe")
    assert ma.main(["--ref", REF, "--board-uid", UID, "--runner-exe", str(tmp_path / "x" / "onefile.exe"),
                    "--out", str(tmp_path / "a.json")]) == 2


# ------------------------------------------------------------------------------------------- GR-2 prompts
def test_plain_prompt_and_pictures_are_shown_with_the_technical_details():
    import hil_operator
    lines = []
    op = hil_operator.ConsoleOperator(input_fn=lambda p: "", print_fn=lines.append, pictures=gate_res.pictures(),
                                      plain=gate_res.plain_prompts())
    op.item = "HG-10cd"
    op.instruct("c3", "Release the MCU reset; wait 2 s (the session reconnects)")
    text = "\n".join(lines)
    assert "Observer: release the RESET button" in text and "details: Release the MCU reset" in text
    assert "B6_hg10cd_hg17_reset_estop.png" in text
    lines.clear()
    op._pic("M-1")                                                       # key-specific beats the item default
    assert any("B2_multimeter_points.png" in x for x in lines) and not any("B6_" in x for x in lines)


def test_prompt_resources_are_consistent():
    pics_map = json.loads((HIL / "resources" / "pictures.json").read_text(encoding="utf-8"))
    bench = HIL.parents[2] / "01_HW" / "diagrams" / "bench"
    for k, v in pics_map.items():
        if k.startswith("_"):
            continue
        assert ":" in k
        for f in (v if isinstance(v, list) else [v]):
            assert (bench / f).is_file(), f
    plain = json.loads((HIL / "resources" / "prompts_plain.json").read_text(encoding="utf-8"))
    for k, v in plain.items():
        if not k.startswith("_"):
            assert ":" in k and v.strip() and len(v) < 400, k

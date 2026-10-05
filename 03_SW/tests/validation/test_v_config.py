"""Level C — configuration read / write / verify / NVM / board-config file (Validator F, M1).

Oracle: the dictionary loaded from ``params.yaml`` (``gen_params.load``), the hard rules H1–H5 written from ICD
§11.4 (``oracle/f_ref.py``), the GET_ALL_PARAMS / SET_PARAM frames on the wire re-parsed by ``ref_codec``.

TC-SW-CFG-001-01, TC-SW-CFG-002-01, TC-SW-CFG-003-01, TC-SW-CFG-004-01 (C part).

Verifies: SW-CFG-001, SW-CFG-002, SW-CFG-003, SW-CFG-004, IF-005, FW-NVM-001 (simulator side)
"""
from __future__ import annotations

import json

import pytest

import harness as H
import ref_codec as rc
from oracle import f_ref

SESSION = ("safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw")


def _p(pdict, key):
    return next(p for p in pdict.params if p.key == key)


def _board_from_wire(be, pdict) -> dict:
    """Board values as last reported on the wire: GET_ALL_PARAMS pages, then later GET/SET_PARAM responses."""
    by_id = {p.id: p.key for p in pdict.params}
    vals: dict = {}
    for w in H.rx(be):
        if w.kind != "response" or w.fields.get("status") != "OK":
            continue
        if w.name == "GET_ALL_PARAMS":
            for e in w.fields["entries"]:
                vals[by_id[e["id"]]] = e["value"]
        elif w.name in ("GET_PARAM", "SET_PARAM"):
            vals[by_id[w.fields["entry"]["id"]]] = w.fields["entry"]["value"]
    return vals


# =================================================================================== SW-CFG-001

@pytest.mark.req("SW-CFG-001")
def test_tc_sw_cfg_001_01_read_all_equals_board_and_metadata_equals_dictionary(vbe, pdict):
    """TC-SW-CFG-001-01: every parameter value = the board's (wire ground truth); metadata (id, type, unit,
    range, default, enum names, M/N/R flags) = params.yaml."""
    # Verifies: SW-CFG-001
    vals = H.result(vbe, H.read_all(vbe))
    wire_vals = _board_from_wire(vbe, pdict)
    assert set(vals) == set(wire_vals) == {p.key for p in pdict.params}
    for k, v in wire_vals.items():
        assert vals[k] == pytest.approx(v), k
    metas = {m.key: m for m in H.config_metas(vbe)}
    assert len(metas) == len(pdict.params)
    for p in pdict.params:
        m = metas[p.key]
        assert m.id == p.id and int(m.type) == rc.PTYPE_CODE[p.type] and m.unit == p.unit, p.key
        assert m.min == pytest.approx(p.min) and m.max == pytest.approx(p.max) and m.default == pytest.approx(p.default)
        assert (m.moving_ok, m.nvm, m.reboot_required) == (p.moving_ok, p.nvm, p.reboot_required), p.key
        if p.type == "enum":
            assert dict(m.enum) == {e.value: e.name for e in p.enum}, p.key
    assert H.config_locked(vbe) == frozenset(k for k in (p.key for p in pdict.params) if not _p(pdict, k).nvm)


# =================================================================================== SW-CFG-002

@pytest.mark.req("SW-CFG-002", "SW-CFG-003")
def test_tc_sw_cfg_002_01_board_file_round_trip(vbe, pdict, tmp_path):
    """Round trip: enums by NAME, f32 exact, dictionary hash recorded, session values excluded (SW-CFG-003);
    recall fills edit fields only — nothing goes on the wire."""
    # Verifies: SW-CFG-002, SW-CFG-003
    vals = H.config_values(vbe)
    vals_edit = dict(vals)
    vals_edit["motion.steps_per_mm"] = f_ref.f32(812.3456789)
    vals_edit["afe.gain_channel"] = 2
    f = tmp_path / "b.bbboard.json"
    H.save_board_file(vbe, f, vals_edit)
    doc = json.loads(f.read_text(encoding="utf-8"))
    assert doc["schema"] == "bird.bend.board" and int(doc["param_dict_hash"], 16) == pdict.hash
    nvm_keys = {p.key for p in pdict.params if p.nvm}
    assert set(doc["params"]) == nvm_keys and not set(SESSION) & set(doc["params"])
    assert doc["params"]["afe.gain_channel"] == "A64" and doc["params"]["afe.rate_sps"] in ("SPS10", "SPS80")
    m0 = H.wire_mark(vbe)
    cfg = H.load_board_file(vbe, f)
    H.advance(vbe, 300)
    assert not H.tx(vbe, "SET_PARAM", since=m0)
    assert not cfg.unknown_keys and not cfg.missing_keys and not cfg.out_of_range and not cfg.hash_mismatch
    for k in nvm_keys:
        assert cfg.values[k] == vals_edit[k], k                       # exact (f32 included)


def _write_doc(tmp_path, name, mutate, base):
    d = json.loads(json.dumps(base))
    mutate(d)
    f = tmp_path / name
    f.write_text(json.dumps(d) if not isinstance(d, str) else d, encoding="utf-8")
    return f


@pytest.mark.req("SW-CFG-002")
def test_tc_sw_cfg_002_01_crafted_files_report_each_case(vbe, pdict, tmp_path):
    """Crafted files: unknown key, missing key, out-of-range, wrong type, hash mismatch are reported per case;
    a newer schema and a truncated file are refused; none of them writes anything."""
    # Verifies: SW-CFG-002
    from bend_stand.core.errors import FileFormatError

    f0 = tmp_path / "base.bbboard.json"
    H.save_board_file(vbe, f0)
    base = json.loads(f0.read_text(encoding="utf-8"))
    rel = _p(pdict, "io.release_ms")
    m0 = H.wire_mark(vbe)

    c = H.load_board_file(vbe, _write_doc(tmp_path, "u.json", lambda d: d["params"].update({"no.such": 1}), base))
    assert "no.such" in c.unknown_keys
    c = H.load_board_file(vbe, _write_doc(tmp_path, "m.json", lambda d: d["params"].pop("io.release_ms"), base))
    assert "io.release_ms" in c.missing_keys and "io.release_ms" not in c.values
    c = H.load_board_file(vbe, _write_doc(tmp_path, "r.json", lambda d: d["params"].update(
        {"io.release_ms": rel.max + 1}), base))
    assert "io.release_ms" in c.out_of_range and "io.release_ms" not in c.values
    c = H.load_board_file(vbe, _write_doc(tmp_path, "t.json", lambda d: d["params"].update(
        {"io.release_ms": "twenty"}), base))
    assert "io.release_ms" in c.out_of_range or "io.release_ms" in c.unknown_keys
    assert "io.release_ms" not in c.values
    c = H.load_board_file(vbe, _write_doc(tmp_path, "e.json", lambda d: d["params"].update(
        {"afe.rate_sps": "SPS999"}), base))
    assert "afe.rate_sps" not in c.values
    c = H.load_board_file(vbe, _write_doc(tmp_path, "h.json", lambda d: d.update(
        {"param_dict_hash": "0x12345678"}), base))
    assert c.hash_mismatch and c.values                               # values matched by key
    with pytest.raises(FileFormatError):
        H.load_board_file(vbe, _write_doc(tmp_path, "n.json", lambda d: d.update({"schema_version": 99}), base))
    trunc = tmp_path / "x.json"
    trunc.write_text(f0.read_text(encoding="utf-8")[:200], encoding="utf-8")
    with pytest.raises(FileFormatError):
        H.load_board_file(vbe, trunc)
    H.advance(vbe, 200)
    assert not H.tx(vbe, "SET_PARAM", since=m0)


# =================================================================================== SW-CFG-003

def _check_rules_after_each_set(be, pdict, before: dict, since: int) -> list[str]:
    """Apply the SET_PARAMs on the wire in order to the board values; every intermediate state keeps H1–H5."""
    by_id = {p.id: p.key for p in pdict.params}
    state = {k: (int(v) if not isinstance(v, float) else v) for k, v in before.items()}
    order = []
    for w in H.tx(be, "SET_PARAM", since=since):
        k = by_id[w.fields["id"]]
        state[k] = w.fields["value"]
        order.append(k)
        assert f_ref.all_rules_ok(state), f"rule broken after SET {k}: {order}"
    return order


@pytest.mark.req("SW-CFG-003", "IF-005")
@pytest.mark.parametrize("case", ["H1", "H3_up", "H3_down", "H4", "H5"])
def test_tc_sw_cfg_003_01_hard_rule_write_order(vbe, pdict, case):
    """Edits that need an order (ICD §11.4) are written so that every single SET keeps H1–H5 true: no E_CONFIG
    on the wire, every item OK, read-back equals the target."""
    # Verifies: SW-CFG-003, IF-005
    # v0.3.3 (OI-B-M3-02, D-45 e: dict 6 defaults rate 40 000 Hz, pulses 12 500 / 12 500 ns = H3 boundary):
    # H3_up = shorter pulses then a higher rate; H3_down = back to the defaults (lower rate first, then longer pulses)
    fast = {"motion.max_step_rate_hz": 50000, "motion.pulse_high_ns": 10000, "motion.pulse_low_min_ns": 10000}
    pre = {"H3_down": fast, "H5": {"afe.timeout_ms": 100}}.get(case)
    if pre:
        assert H.result(vbe, H.write_verify(vbe, pre)).ok
    edits = {"H1": {"limits.soft_min_um": 300000, "limits.soft_max_um": 399999},
             "H3_up": fast,
             "H3_down": {"motion.max_step_rate_hz": 40000, "motion.pulse_high_ns": 12500,
                         "motion.pulse_low_min_ns": 12500},
             "H4": {"motion.v_max_load_um_s": 40000, "motion.v_max_travel_um_s": 50000},
             "H5": {"afe.rate_sps": 0, "afe.timeout_ms": 300}}[case]
    before = H.config_values(vbe)
    m0 = H.wire_mark(vbe)
    rep = H.result(vbe, H.write_verify(vbe, edits))
    assert {i.key: str(i.status) for i in rep.items} == {k: "OK" for k in edits}, rep
    order = _check_rules_after_each_set(vbe, pdict, before, m0)
    assert sorted(order) == sorted(edits)
    nacks = [w for w in H.rx(vbe, "SET_PARAM", since=m0) if w.fields["status"] != "OK"]
    assert not nacks
    assert H.tx(vbe, "GET_ALL_PARAMS", since=m0)                     # read-back
    vals = H.config_values(vbe)
    assert all(vals[k] == v for k, v in edits.items())


@pytest.mark.req("SW-CFG-003")
@pytest.mark.parametrize("edits, code", [
    ({"limits.soft_min_um": 300000}, "RULE_H1"),                       # 300000 ≥ soft_max 290000
    ({"motion.pulse_high_ns": 60000}, "RULE_H3"),                      # 40000 · 72500 > 1e9 (dict 6)
    ({"io.release_ms": 201}, "RANGE"),                                 # max 200
    ({"afe.rate_sps": 7}, "RANGE"),                                    # undefined enum code
    ({"safety.zero_raw": 5}, "LOCKED"),                                # session value (SAF-SW-002 owner)
    ({"no.such.key": 1}, "UNKNOWN_KEY"),
], ids=["H1", "H3", "range", "enum", "session", "unknown"])
def test_tc_sw_cfg_003_01_refused_before_the_first_write(vbe, edits, code):
    """Local check errors abort before the first SET_PARAM (nothing on the wire); session keys are refused
    ('managed by the backend')."""
    # Verifies: SW-CFG-003
    issues = H.config_check(vbe, edits)
    assert any(i.code == code and str(i.severity) == "ERROR" for i in issues), issues
    m0 = H.wire_mark(vbe)
    rep = H.result(vbe, H.write_verify(vbe, edits))
    assert not rep.ok
    assert all(str(i.status) == "NOT_ATTEMPTED" for i in rep.items)
    H.advance(vbe, 100)
    assert not H.tx(vbe, "SET_PARAM", since=m0)


@pytest.mark.req("SW-CFG-003", "IF-005")
def test_tc_sw_cfg_003_01_rejected_mismatch_timeout(vbe, pdict):
    """Injected NACK → REJECTED with the FW status/detail; injected store mismatch → MISMATCH; three dropped
    responses → TIMEOUT after exactly 1 + 2 retries, each with a new SEQ (ICD §9.3 RETRY class)."""
    # Verifies: SW-CFG-003, IF-005
    H.inject_nack(vbe, "SET_PARAM", "E_RANGE", _p(pdict, "io.release_ms").id)
    rep = H.result(vbe, H.write_verify(vbe, {"io.release_ms": 50}))
    it = rep.by_key()["io.release_ms"]
    assert str(it.status) == "REJECTED" and it.nack_status == rc.STATUS["E_RANGE"]
    assert it.nack_detail == _p(pdict, "io.release_ms").id
    H.inject_store_mismatch(vbe, "io.estop_release_ms", 150)
    rep = H.result(vbe, H.write_verify(vbe, {"io.estop_release_ms": 140}))
    it = rep.by_key()["io.estop_release_ms"]
    assert str(it.status) == "MISMATCH" and it.stored == 150
    H.act(vbe, "inject", fault="drop_next", cmd="SET_PARAM", what="response", n=3)
    m0 = H.wire_mark(vbe)
    rep = H.result(vbe, H.write_verify(vbe, {"io.release_ms": 60}))
    assert str(rep.by_key()["io.release_ms"].status) == "TIMEOUT"
    sets = H.tx(vbe, "SET_PARAM", since=m0)
    assert len(sets) == 3 and len({w.seq for w in sets}) == 3
    assert all(w.fields["value"] == 60 for w in sets)


@pytest.mark.req("SW-CFG-003", "SW-CFG-004")
def test_tc_sw_cfg_003_01_reboot_required_flow(vbe, pdict):
    """motion.pul_invert (R): write → REBOOT_REQUIRED + REBOOT_PENDING; SAVE; REBOOT (VERIFY class: one frame)
    → EVENT BOOT → automatic resync without enable/home/move; the value survived (NVM); REBOOT_PENDING clear."""
    # Verifies: SW-CFG-003, SW-CFG-004
    rep = H.result(vbe, H.write_verify(vbe, {"motion.pul_invert": True}))
    assert str(rep.by_key()["motion.pul_invert"].status) == "REBOOT_REQUIRED" and rep.reboot_pending is True
    assert H.status(vbe).reboot_pending is True
    H.result(vbe, H.save_nvm(vbe))
    m0 = H.wire_mark(vbe)
    H.result(vbe, H.reboot(vbe))
    assert H.run_until(vbe, lambda: H.status(vbe).stream.on and H.status(vbe).reboot_pending is False, 5000)
    H.advance(vbe, 500)
    assert len(H.tx(vbe, "REBOOT", since=m0)) == 1
    assert any(w.name == "EVENT" and w.fields["code"] == "BOOT" for w in H.rx(vbe, since=m0))
    names = [w.name for w in H.tx(vbe, since=m0)]
    assert not {"ENABLE", "HOME", "MOVE_ABS", "JOG", "MOVE_UNTIL_LOAD", "RESUME"} & set(names)
    assert "GET_ALL_PARAMS" in names and "STREAM_START" in names
    assert H.config_values(vbe)["motion.pul_invert"] in (True, 1)


# =================================================================================== SW-CFG-004

@pytest.mark.req("SW-CFG-004", "IF-005")
def test_tc_sw_cfg_004_01_save_load_defaults_cfg_dirty(vbe, pdict):
    """SAVE → nvm_record_seq + 1 and CFG_DIRTY 0; edit → CFG_DIRTY 1; LOAD → NVM value back, CFG_DIRTY 0;
    DEFAULTS → every parameter at its params.yaml default, session values re-sent / verified by the SW
    (ICD §5.2, §11.2, §11.5)."""
    # Verifies: SW-CFG-004, IF-005
    H.advance(vbe, 1200)
    seq0 = H.status(vbe).board.nvm_record_seq
    assert H.result(vbe, H.write_verify(vbe, {"io.release_ms": 33})).ok
    assert H.status(vbe).cfg_dirty is True
    H.result(vbe, H.save_nvm(vbe))
    st = H.status(vbe)
    assert st.board.nvm_record_seq == seq0 + 1 and st.cfg_dirty is False
    assert H.result(vbe, H.write_verify(vbe, {"io.release_ms": 44})).ok
    assert H.status(vbe).cfg_dirty is True
    vals = H.result(vbe, H.load_nvm(vbe))
    assert vals["io.release_ms"] == 33 and H.config_values(vbe)["io.release_ms"] == 33
    H.advance(vbe, 100)
    assert H.status(vbe).cfg_dirty is False
    m0 = H.wire_mark(vbe)
    vals = H.result(vbe, H.defaults(vbe))
    assert len(H.tx(vbe, "DEFAULT_PARAMS", since=m0)) == 1
    for p in pdict.params:
        assert vals[p.key] == pytest.approx(p.default), p.key
    ids = {p.key: p.id for p in pdict.params}
    i_def = next(i for i, w in enumerate(H.wire(vbe, m0)) if w.name == "DEFAULT_PARAMS")
    later = [w for w in H.wire(vbe, m0)[i_def:] if w.dir == "TX" and w.name == "GET_PARAM"]
    assert {ids[k] for k in SESSION} <= {w.fields["id"] for w in later}
    assert H.thresholds(vbe).state in ("DEFAULT_ONLY", "VERIFIED")
    H.advance(vbe, 100)
    assert H.status(vbe).cfg_dirty is True                           # RAM (defaults) ≠ saved record (33)


@pytest.mark.req("SW-CFG-004", "IF-005")
def test_tc_sw_cfg_004_01_power_cut_during_save_keeps_previous_record(vbe, pdict):
    """NVM: a power cut during SAVE (S variant: record-level cut, board resets) keeps the previous record; the
    SW resynchronises after EVENT BOOT and shows the previous value; nothing is re-sent automatically."""
    # Verifies: SW-CFG-004, IF-005
    assert H.result(vbe, H.write_verify(vbe, {"io.release_ms": 55})).ok
    H.result(vbe, H.save_nvm(vbe))
    assert H.result(vbe, H.write_verify(vbe, {"io.release_ms": 66})).ok
    H.act(vbe, "flash", cut_after_word=3)
    m0 = H.wire_mark(vbe)
    try:
        H.result(vbe, H.save_nvm(vbe))
    except Exception:  # noqa: BLE001 - the outcome after a power cut may be 'not confirmed'
        pass
    assert H.run_until(vbe, lambda: any(w.name == "EVENT" and w.fields.get("code") == "BOOT"
                                        for w in H.rx(vbe, since=m0)), 5000)
    assert H.run_until(vbe, lambda: H.status(vbe).stream.on and H.config_values(vbe).get("io.release_ms") == 55,
                       5000)
    assert len(H.tx(vbe, "SAVE_PARAMS", since=m0)) == 1


@pytest.mark.req("SW-CFG-003")
@pytest.mark.defect("SWD-M1-08")
def test_tc_sw_cfg_003_01_non_moving_ok_key_while_moving_is_busy(vbe):
    """While the axis moves (forced path — motion is M2): a non-`moving_ok` key gets status BUSY without being
    sent; a `moving_ok` key in the same edit set is written and verified (SW_design §5.3, SRS SW-CFG-003)."""
    # Verifies: SW-CFG-003
    H.forced_enable_home(vbe)
    H.result(vbe, H.forced_move_abs(vbe, 200_000))
    H.advance(vbe, 50)
    m0 = H.wire_mark(vbe)
    rep = H.result(vbe, H.write_verify(vbe, {"io.release_ms": 50, "stream.fallback_hz": 20}))
    st = {i.key: str(i.status) for i in rep.items}
    assert st == {"io.release_ms": "BUSY", "stream.fallback_hz": "OK"}, st
    assert [w.fields["value"] for w in H.tx(vbe, "SET_PARAM", since=m0)] == [20]

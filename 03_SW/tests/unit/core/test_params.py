"""ParamStore, local checks, write plan and the board-config file (``*.bbboard.json``).

Verifies: SW-CFG-001, SW-CFG-002, SW-CFG-003
"""
from __future__ import annotations

import json

import pytest

from bend_stand.core import params_gen as pgen
from bend_stand.core.errors import FileFormatError
from bend_stand.core.model import IssueSeverity
from bend_stand.core.params import (
    LOCKED_KEYS, ParamStore, check_edits, load_board_config, normalise, save_board_config, write_plan,
)
from bend_stand.core.schema import atomic_write_json, read_json, unknown_keys

DEFAULTS = {p.key: p.default for p in pgen.PARAMS}


@pytest.mark.req("SW-CFG-001")
def test_store_and_metadata() -> None:
    s = ParamStore()
    assert not s.read_ok and s.values() == {}
    s.set_all(DEFAULTS)
    s.update("afe.rate_tol_pct", 30)
    assert s.read_ok and s.get("afe.rate_tol_pct") == 30 and s.get("x", 1) == 1
    assert len(s.metas()) == pgen.PARAM_COUNT and s.groups() == pgen.GROUPS and s.defaults() == DEFAULTS
    s.clear()
    assert s.values() == {}
    assert LOCKED_KEYS == {"safety.load_raw_min", "safety.load_raw_max", "safety.zero_raw"}


@pytest.mark.req("SW-CFG-003")
@pytest.mark.parametrize("key, raw, expect", [
    ("afe.rate_sps", "SPS10", 0), ("afe.rate_sps", 1, 1), ("afe.rate_sps", 1.0, 1), ("motion.pul_invert", 1, True),
    ("motion.pul_invert", False, False), ("motion.steps_per_mm", 800, 800.0), ("afe.timeout_ms", 300.0, 300),
])
def test_normalise(key: str, raw, expect) -> None:
    v = normalise(pgen.BY_KEY[key], raw)
    assert v == expect and type(v) is type(expect)


@pytest.mark.req("SW-CFG-003")
@pytest.mark.parametrize("key, raw", [
    ("afe.rate_sps", "SPS99"), ("afe.rate_sps", True), ("motion.pul_invert", 2), ("motion.steps_per_mm", "x"),
    ("motion.steps_per_mm", float("inf")), ("afe.timeout_ms", 1.5), ("afe.timeout_ms", "1"), ("afe.timeout_ms", True),
])
def test_normalise_rejects(key: str, raw) -> None:
    with pytest.raises((ValueError, KeyError)):
        normalise(pgen.BY_KEY[key], raw)


@pytest.mark.req("SW-CFG-003")
def test_check_edits_cases() -> None:
    codes = lambda iss: sorted({(i.key, i.code) for i in iss})  # noqa: E731
    assert check_edits(DEFAULTS, {"afe.rate_tol_pct": 30}) == []
    iss = check_edits(DEFAULTS, {"afe.rate_tol_pct": 99, "zz.y": 1, "safety.zero_raw": 5, "afe.rate_sps": "X"})
    assert codes(iss) == [("afe.rate_sps", "TYPE"), ("afe.rate_tol_pct", "RANGE"), ("safety.zero_raw", "LOCKED"),
                          ("zz.y", "UNKNOWN_KEY")]
    iss = check_edits(DEFAULTS, {"limits.soft_min_um": 300_000})
    assert codes(iss) == [("limits.soft_min_um", "RULE_H1")]
    iss = check_edits(DEFAULTS, {"afe.rate_sps": 0, "afe.timeout_ms": 100})
    assert {i.code for i in iss} == {"RULE_H5"} and len(iss) == 2
    iss = check_edits(DEFAULTS, {"motion.steps_per_mm": 900.0, "afe.rate_tol_pct": 25}, moving=True)
    assert codes(iss) == [("motion.steps_per_mm", "MOVING")]
    iss = check_edits(DEFAULTS, {"motion.pul_invert": True})
    assert iss[0].severity == IssueSeverity.INFO and iss[0].code == "REBOOT_REQUIRED"
    iss = check_edits(DEFAULTS, {"safety.link_timeout_ms": 500})
    assert iss[0].code == "LINK_TIMEOUT" and iss[0].severity == IssueSeverity.WARN


@pytest.mark.req("SW-CFG-003")
def test_write_plan_only_changes_in_rule_order() -> None:
    cur = dict(DEFAULTS, **{"limits.soft_min_um": 500, "limits.soft_max_um": 290_000})
    plan = write_plan(cur, {"limits.soft_min_um": 295_000, "limits.soft_max_um": 300_000, "afe.rate_tol_pct": 20})
    assert plan == [("limits.soft_max_um", 300_000), ("limits.soft_min_um", 295_000)]


@pytest.mark.req("SW-CFG-002")
def test_board_config_round_trip(tmp_path) -> None:
    vals = dict(DEFAULTS, **{"afe.rate_sps": 0, "afe.timeout_ms": 300, "motion.steps_per_mm": 636.0778,
                             "motion.pul_invert": True})
    p = tmp_path / "a.bbboard.json"
    save_board_config(str(p), vals, sw_version="1", fw_version="0.1.0", board_uid="AB", saved_utc="t")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["schema"] == "bird.bend.board" and d["params"]["afe.rate_sps"] == "SPS10"
    assert "safety.zero_raw" not in d["params"]                  # session values never saved
    bc = load_board_config(str(p))
    assert not bc.hash_mismatch and bc.unknown_keys == bc.out_of_range == () and bc.missing_keys == ()
    for k, v in bc.values.items():
        assert v == normalise(pgen.BY_KEY[k], vals[k])           # f32 exact, enums by name, bools
    assert bc.values["motion.steps_per_mm"] == pytest.approx(636.0778, rel=1e-6)


@pytest.mark.req("SW-CFG-002")
def test_board_config_report_cases(tmp_path) -> None:
    p = tmp_path / "b.bbboard.json"
    atomic_write_json(p, {"schema": "bird.bend.board", "schema_version": 1, "param_dict_hash": "0x1",
                          "extra": 1, "params": {"afe.rate_tol_pct": 99, "home.ref_switch": "START",
                                                 "afe.rate_sps": "SPS10", "safety.zero_raw": 3}})
    bc = load_board_config(str(p))
    assert bc.hash_mismatch and bc.file_hash == 1
    assert set(bc.unknown_keys) == {"home.ref_switch", "safety.zero_raw"} and bc.out_of_range == ("afe.rate_tol_pct",)
    assert bc.values == {"afe.rate_sps": 0} and len(bc.missing_keys) > 0
    assert {i.code for i in bc.issues} >= {"UNKNOWN_FIELD", "HASH_MISMATCH", "RANGE", "UNKNOWN_KEY", "MISSING"}


@pytest.mark.req("SW-CFG-002")
def test_schema_errors(tmp_path) -> None:
    p = tmp_path / "c.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(FileFormatError):
        read_json(p, "board", 1)
    atomic_write_json(p, {"schema": "bird.bend.other", "schema_version": 1})
    with pytest.raises(FileFormatError):
        read_json(p, "board", 1)
    atomic_write_json(p, {"schema": "bird.bend.board", "schema_version": 2, "params": {}})
    with pytest.raises(FileFormatError, match="newer"):
        load_board_config(str(p))
    atomic_write_json(p, {"schema": "bird.bend.board"})
    with pytest.raises(FileFormatError):
        read_json(p, "board", 1)
    atomic_write_json(p, {"schema": "bird.bend.board", "schema_version": 1})
    with pytest.raises(FileFormatError):
        load_board_config(str(p))
    assert unknown_keys({"schema": 1, "a": 1, "b": 2}, ["a"]) == ["b"]

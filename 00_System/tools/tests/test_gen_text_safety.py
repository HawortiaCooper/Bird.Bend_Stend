"""Generator input hardening (FWR-14, Validator E's FW_code_review.md): every YAML string that reaches generated C /
Python passes an identifier, version, integer or free-text check in ``load()`` (rules in gen_protocol.py "text and
identifier safety"). Validator E's ``02_FW/test/static/test_val_review_gen.py`` covers the reported injection
paths; these cases cover the rest of the rule set (line separators, bidi controls, trigraphs, backslashes,
keywords, numeric fields) and prove that the real dictionaries still load unchanged.

Implements: FW-CFG-001, IF-010 (generated code is a faithful, inert rendering of the dictionaries)
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import gen_params as gp  # noqa: E402
import gen_protocol as gpr  # noqa: E402

SPECS = TOOLS.parents[1] / "00_System" / "specs"
PARAMS_DOC = yaml.safe_load((SPECS / "params.yaml").read_text(encoding="utf-8"))
PROTO_DOC = yaml.safe_load((SPECS / "protocol.yaml").read_text(encoding="utf-8"))

BAD_TEXT = ["a\nb", "a\rb", "a\tb", "a b", "a b", "a\x85b", "a‮b", "a﻿b", "a\x00b",
            "a */ b", "a /* b", "a\\b", 'a"""b', "a ??/ b", "a ??= b"]


def _write(tmp_path: Path, name: str, doc: dict) -> Path:
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p


def _params_with(tmp_path: Path, edit) -> None:  # noqa: ANN001
    doc = copy.deepcopy(PARAMS_DOC)
    edit(doc)
    with pytest.raises(gp.DictError):
        gp.load(_write(tmp_path, "params.yaml", doc))


def _proto_with(tmp_path: Path, edit) -> None:  # noqa: ANN001
    doc = copy.deepcopy(PROTO_DOC)
    edit(doc)
    with pytest.raises(gpr.ProtoError):
        gpr.load(_write(tmp_path, "protocol.yaml", doc))


def test_real_dictionaries_load_and_regenerate_unchanged():
    """The checks accept every current text (incl. non-ASCII µ, §, →, ≥); outputs stay byte-identical."""
    d, p = gp.load(), gpr.load()
    for path, content in gp.outputs(d, p).items():
        assert path.read_text(encoding="utf-8") == content, path
    assert d.hash == 0xF8BCDCB8


@pytest.mark.parametrize("text", BAD_TEXT)
def test_free_text_rule(text):
    with pytest.raises(gpr.ProtoError):
        gpr.safe_text(text, "t")
    with pytest.raises(gp.DictError):
        gpr.safe_text(text, "t", gp.DictError)


def test_free_text_rule_keeps_inert_text():
    for ok in ("µm/s", "ICD §5.4 → E_RANGE ≥ 1", 'he said "x"', "a / b * c", "??", "50 % FS", "x'y"):
        assert gpr.safe_text(ok, "t") == ok


@pytest.mark.parametrize("field", ["label", "description", "unit"])
def test_param_text_fields(tmp_path, field):
    bad = {"label": "x y", "description": "x */ y", "unit": "mm\\"}[field]

    def edit(doc):
        doc["groups"][1]["params"][0][field] = bad
    _params_with(tmp_path, edit)


def test_param_description_whitespace_is_normalised(tmp_path):
    """Descriptions are whitespace-normalised (line breaks / separators → one space) before the check: inert."""
    doc = copy.deepcopy(PARAMS_DOC)
    doc["groups"][1]["params"][0]["description"] = "line one\nline two\tend"
    d = gp.load(_write(tmp_path, "params.yaml", doc))
    key = f"{doc['groups'][1]['group']}.{doc['groups'][1]['params'][0]['name']}"
    assert next(p for p in d.params if p.key == key).description == "line one line two end"


def test_param_group_label_and_srs(tmp_path):
    _params_with(tmp_path, lambda d: d["groups"][0].__setitem__("label", "AFE */"))
    _params_with(tmp_path, lambda d: d["groups"][0]["params"][0].__setitem__("srs", ["SAF-FW-001\nX = 1"]))


@pytest.mark.parametrize("member", ["int", "class", "Afe-1", "afe x", "1afe", "afé"])
def test_param_c_member_identifier(tmp_path, member):
    _params_with(tmp_path, lambda d: d["groups"][0].__setitem__("c_member", member))


@pytest.mark.parametrize("dv", ["6", 6.0, True, 0, -1, None])
def test_param_dict_version_integer(tmp_path, dv):
    _params_with(tmp_path, lambda d: d.__setitem__("dict_version", dv))


@pytest.mark.parametrize("v", ["0.7", "0.7.5a", "v0.7.5", "0.7.5 ", 75, "0..7"])
def test_proto_icd_version(tmp_path, v):
    if v == "0.7":
        assert gpr.safe_version(v, "x") == v                        # two-part versions are valid
        return
    _proto_with(tmp_path, lambda d: d.__setitem__("icd_version", v))


@pytest.mark.parametrize("key", ["proto_major", "proto_minor", "payload_version"])
def test_proto_version_numbers_are_integers(tmp_path, key):
    _proto_with(tmp_path, lambda d: d.__setitem__(key, "1\nX = 2"))


def test_proto_py_names_and_columns(tmp_path):
    _proto_with(tmp_path, lambda d: d["tables"][0].__setitem__("py_name", "class"))
    _proto_with(tmp_path, lambda d: d["tables"][0].__setitem__("py_name", "Async(int); X"))
    _proto_with(tmp_path, lambda d: d["commands"].__setitem__("py_name", "Cmd:"))
    _proto_with(tmp_path, lambda d: d["commands"].__setitem__("c_prefix", "CMD_ X"))
    tid = next(i for i, t in enumerate(PROTO_DOC["tables"]) if t.get("columns"))
    _proto_with(tmp_path, lambda d: d["tables"][tid].__setitem__("columns", ["detail; X"]))


def test_proto_text_fields(tmp_path):
    _proto_with(tmp_path, lambda d: d["tables"][0].__setitem__("title", "T\\"))
    _proto_with(tmp_path, lambda d: d["tables"][0]["items"][0].__setitem__("desc", "a */ b"))
    ev = next(i for i, t in enumerate(PROTO_DOC["tables"]) if t["kind"] == "events")
    _proto_with(tmp_path, lambda d: d["tables"][ev]["items"][0].__setitem__("arg", "x‮y"))
    _proto_with(tmp_path, lambda d: d["commands"]["items"][0].__setitem__("request", "a ??/ b"))
    _proto_with(tmp_path, lambda d: d["tables"][0]["items"][0].__setitem__("retired", "0.5 */"))

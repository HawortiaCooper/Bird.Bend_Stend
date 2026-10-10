"""Validator E - code review FWR-14 (02_FW/docs/FW_code_review.md): text from params.yaml / protocol.yaml that
reaches generated C / Python code without validation or escaping (gen_params.py, gen_protocol.py @e600169).

    .venv\\Scripts\\python -m pytest 02_FW\\test\\static\\test_val_review_gen.py -q

The generators are run in memory on a modified COPY of the real YAML (tmp dir); nothing in the repo is written.
Safe behaviour asserted: a hostile string is either rejected (DictError / ProtoError) or stays inert (inside a
comment / string literal). At e600169 the marked cases fail - that is the finding's evidence.
Verifies: FW-CFG-001, IF-010 (generated code is a faithful, inert rendering of the dictionaries)
TC: TC-FW-CFG-001-02
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "00_System" / "tools"
sys.path.insert(0, str(TOOLS))

import gen_params as gp  # noqa: E402
import gen_protocol as gpr  # noqa: E402

MARK = "INJECTED_BY_YAML"


def _strip_c_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"//[^\n]*", " ", src)


def _params_doc() -> dict:
    return yaml.safe_load((ROOT / "00_System" / "specs" / "params.yaml").read_text(encoding="utf-8"))


def _proto_doc() -> dict:
    return yaml.safe_load((ROOT / "00_System" / "specs" / "protocol.yaml").read_text(encoding="utf-8"))


def _load_params(tmp_path: Path, doc: dict):
    p = tmp_path / "params.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return gp.load(p)


def _load_proto(tmp_path: Path, doc: dict):
    p = tmp_path / "protocol.yaml"
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return gpr.load(p)


def _first_param(doc: dict, pred) -> dict:
    for g in doc["groups"]:
        for prm in g["params"]:
            if pred(prm):
                return prm
    raise AssertionError("no such parameter")


def _c_injected(src: str) -> bool:
    return MARK in _strip_c_comments(src)


def _py_injected(src: str) -> bool:
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise AssertionError(f"generated Python no longer parses after the YAML edit (FWR-14): {e}") from None
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == MARK:
            return True
    return False


# ------------------------------------------------------------------------------------------- params.yaml
def _rejected_or_inert_params(tmp_path, doc, render) -> None:
    try:
        d = _load_params(tmp_path, doc)
    except gp.DictError:
        return                                        # rejected: safe
    out = render(d)
    if render is gp.gen_python:
        assert not _py_injected(out), "YAML text became Python code in params_gen.py (FWR-14)"
    else:
        assert not _c_injected(out), "YAML text became C code in params_gen.h/.c (FWR-14)"


def test_fwr14_param_unit_closes_c_comment(tmp_path):
    doc = _params_doc()
    _first_param(doc, lambda p: p.get("unit") == "ms")["unit"] = f"ms */ int {MARK}; /*"
    _rejected_or_inert_params(tmp_path, doc, gp.gen_c_header)


def test_fwr14_enum_label_closes_c_comment(tmp_path):
    doc = _params_doc()
    _first_param(doc, lambda p: p.get("type") == "enum")["enum"][0]["label"] = f"x */ int {MARK}; /*"
    _rejected_or_inert_params(tmp_path, doc, gp.gen_c_header)


def test_fwr14_c_member_is_not_an_identifier(tmp_path):
    doc = _params_doc()
    doc["groups"][0]["c_member"] = f"afe; int {MARK}"
    _rejected_or_inert_params(tmp_path, doc, gp.gen_c_header)


def test_fwr14_dict_version_not_an_integer_python(tmp_path):
    doc = _params_doc()
    doc["dict_version"] = f"6\n{MARK} = 1"
    _rejected_or_inert_params(tmp_path, doc, gp.gen_python)


# ------------------------------------------------------------------------------------------- protocol.yaml
def _rejected_or_inert_proto(tmp_path, doc, render, lang) -> None:
    try:
        p = _load_proto(tmp_path, doc)
    except gpr.ProtoError:
        return
    out = render(p)
    if lang == "py":
        assert not _py_injected(out), "YAML text became Python code in protocol_gen.py (FWR-14)"
    else:
        assert not _c_injected(out), "YAML text became C code in proto_gen.h (FWR-14)"


def _render(name):
    for n in (name, name.replace("gen_", "gen_c_"), name.replace("gen_", "gen_py_")):
        if hasattr(gpr, n):
            return getattr(gpr, n)
    raise AssertionError(f"gen_protocol has no {name}")


def _c_header():
    for n in ("gen_c_header", "gen_c", "gen_header"):
        if hasattr(gpr, n):
            return getattr(gpr, n)
    raise AssertionError("no C renderer")


def _python():
    for n in ("gen_python", "gen_py"):
        if hasattr(gpr, n):
            return getattr(gpr, n)
    raise AssertionError("no Python renderer")


def test_fwr14_constant_desc_newline_into_python(tmp_path):
    doc = _proto_doc()
    doc["constants"][0]["desc"] = f"sync byte\n{MARK} = 1"
    _rejected_or_inert_proto(tmp_path, doc, _python(), "py")


def test_fwr14_table_title_closes_python_docstring(tmp_path):
    doc = _proto_doc()
    doc["tables"][0]["title"] = f'x"""\n    {MARK} = 1\n    """'
    _rejected_or_inert_proto(tmp_path, doc, _python(), "py")


def test_fwr14_icd_version_breaks_c_string(tmp_path):
    doc = _proto_doc()
    doc["icd_version"] = f'0.7.4"\nint {MARK};\n#define X "'
    _rejected_or_inert_proto(tmp_path, doc, _c_header(), "c")


def test_fwr14_table_icd_ref_closes_c_comment(tmp_path):
    doc = _proto_doc()
    doc["tables"][0]["icd"] = f"§7 */ int {MARK}; /*"
    _rejected_or_inert_proto(tmp_path, doc, _c_header(), "c")


def test_control_real_yaml_renders_inert(tmp_path):
    """Control: the real dictionaries contain no such text (the current generated code is fine)."""
    d = gp.load()
    p = gpr.load()
    assert not _c_injected(gp.gen_c_header(d)) and not _py_injected(gp.gen_python(d))
    assert not _c_injected(_c_header()(p)) and not _py_injected(_python()(p))

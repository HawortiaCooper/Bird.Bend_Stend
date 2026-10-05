"""Levels U / I — pure calculations against R4 §12 (M1 subset), layering, ICD-name inspection, serial settings,
generators, traceability and packaging (Validator F, M1).

Expected values: R4 §12 vectors (copied verbatim from R4, proved self-consistent at P1 by ``oracle/f_ref.py``),
the ICD tables in ``ref_codec`` (oracle), SRS IF-002 / SW-PLT-001 text.

TC-SW-PLT-002-01, TC-SW-PLT-002-02 (M1: TV-U / TV-TC units / TV-L / TV-M planner), TC-IF-001-01, TC-IF-002-01,
TC-IF-010-01, TC-SYS-010-01, TC-SW-PLT-001-01 (inspection part).

Verifies: SW-PLT-001, SW-PLT-002, IF-001, IF-002, IF-010, SYS-010, SYS-003
"""
from __future__ import annotations

import ast
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

import ref_codec as rc
from oracle import f_ref

SW = Path(__file__).resolve().parents[2]
REPO = SW.parent
SRC = SW / "src" / "bend_stand"
TOOLS = REPO / "00_System" / "tools"
GENERATED = {"params_gen.py", "protocol_gen.py"}


def _modules(*subs: str) -> list[Path]:
    out = []
    for s in subs:
        out += [p for p in (SRC / s).rglob("*.py") if p.name not in GENERATED]
    return out


# ============================================================================================ SW-PLT-002

@pytest.mark.req("SW-PLT-002")
def test_tc_sw_plt_002_01_backend_imports_without_qt():
    """calc / io / core (incl. the simulator) import in a fresh interpreter in which PySide6, shiboken6 and
    pyqtgraph are unimportable."""
    # Verifies: SW-PLT-002
    code = r"""
import sys, importlib, pkgutil
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('PySide6', 'shiboken6', 'pyqtgraph'):
            raise ImportError('blocked: ' + name)
        return None
sys.meta_path.insert(0, Block())
import bend_stand
n = 0
for sub in ('calc', 'io', 'core'):
    pkg = importlib.import_module('bend_stand.' + sub)
    for m in pkgutil.walk_packages(pkg.__path__, 'bend_stand.' + sub + '.'):
        importlib.import_module(m.name); n += 1
assert not [k for k in sys.modules if k.split('.')[0] in ('PySide6', 'shiboken6', 'pyqtgraph')]
print('OK', n)
"""
    r = subprocess.run([sys.executable, "-c", code], cwd=str(SW / "src"), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.startswith("OK"), r.stderr[-2000:]
    assert int(r.stdout.split()[1]) >= 30


@pytest.mark.req("SW-PLT-002")
def test_tc_sw_plt_002_01_calc_is_pure():
    """calc: no threads, clocks, files, sockets or serial — pure functions (AST)."""
    # Verifies: SW-PLT-002
    banned_mod = {"threading", "time", "socket", "serial", "os", "subprocess", "pathlib", "io", "logging"}
    bad = []
    for p in _modules("calc"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                bad += [f"{p.name}:{a.name}" for a in node.names if a.name.split(".")[0] in banned_mod]
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in banned_mod:
                bad.append(f"{p.name}:{node.module}")
            elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                bad.append(f"{p.name}:open()")
    assert not bad, bad


@pytest.mark.req("SW-PLT-002", "SYS-003")
def test_tc_sw_plt_002_02_r4_tv_tc_um_steps():
    """R4 TV-TC µm↔steps vectors (spm 636.0778…) on production calc.motion (binary64, round half away)."""
    # Verifies: SW-PLT-002, SYS-003
    from bend_stand.calc import motion

    spm = 636.0778443113772
    assert [motion.um_to_steps(u, spm) for u in (12345, -500, 100)] == [7852, -318, 64]
    assert [motion.steps_to_um(s, spm) for s in (7852, -318, 64)] == [12344, -500, 101]


@pytest.mark.req("SW-PLT-002", "IF-006")
def test_tc_sw_plt_002_02_r4_tv_l_unwrap_and_budget():
    """R4 TV-L: t_us unwrap across the 2³² wrap (production calc.timebase); link budget per ICD §10
    (26-byte DATA frame, 2.26 %; R4's 22-byte value is the SWD-P1-13 erratum)."""
    # Verifies: SW-PLT-002, IF-006
    from bend_stand.calc import timebase

    assert timebase.unwrap_us([4294967000, 4294967290, 200, 12700]) == [4294967000, 4294967290, 4294967496,
                                                                         4294979996]
    assert f_ref.unwrap_us([4294967000, 4294967290, 200, 12700]) == [4294967000, 4294967290, 4294967496,
                                                                      4294979996]
    frame = rc.OVERHEAD + rc.DATA_LEN
    assert frame == 26 and frame * 80 / 92_160 == pytest.approx(0.02257, abs=1e-4)
    assert 2**32 / 1e6 / 60 == pytest.approx(71.58278826666667)


@pytest.mark.req("SW-PLT-002", "IF-007")
@pytest.mark.parametrize("prev, seq, kind, lost", [(None, 5, "first", 0), (5, 6, "next", 0), (5, 9, "lost", 3),
                                                   (65535, 1, "lost", 1), (65535, 0, "next", 0), (7, 7, "dup", 0),
                                                   (100, 50, "anomaly", 0)])
def test_tc_sw_plt_002_02_frame_gap_rule(prev, seq, kind, lost):
    """u16 frame_seq rule (SWD-P1-12 b as designed): 65535 → 1 counts 1 lost; duplicate = d 0; backward =
    anomaly (no 65 535 losses)."""
    # Verifies: SW-PLT-002, IF-007
    from bend_stand.calc import timebase

    st = timebase.frame_gap(prev, seq)
    assert (st.kind, st.lost) == (kind, lost)


@pytest.mark.req("SW-PLT-002")
def test_tc_sw_plt_002_02_r4_tv_m_planner():
    """R4 TV-M planner vectors (64 000 steps trapezoid; 1 000 steps asymmetric triangle) on production
    calc.motion.plan_trapezoid; the ramp-period vectors are FW-side (M2 motion_vectors)."""
    # Verifies: SW-PLT-002
    from bend_stand.calc import motion

    V, A = 12800.0, 64000.0
    t = motion.plan_trapezoid(64000, V, A, A)
    assert (t["kind"], t["n_acc"], t["n_cruise"], t["n_dec"]) == ("trap", 1280, 61440, 1280)
    assert t["v_peak"] == 12800.0 and t["t"] == pytest.approx(5.2)
    t = motion.plan_trapezoid(1000, V, A, 2 * A)
    assert (t["kind"], t["n_acc"], t["n_dec"]) == ("tri", 667, 333)
    assert t["v_peak"] == pytest.approx(9237.604307034011) and t["t"] == pytest.approx(0.21650635094610965)
    assert V * V / (2 * A) / 640 == pytest.approx(2.0)
    assert motion.stop_distance_um(20_000, 100_000) == pytest.approx(2000.0)
    assert motion.move_duration_s(10_000, 2_000, 100_000) == pytest.approx(5.02)       # VV-SEQ-04
    assert motion.move_duration_s(50_000, 10_000, 100_000) == pytest.approx(5.10)      # VV-SEQ-04


# ============================================================================================ IF-001

def _attr_uses(paths, owner_names, enum_names):
    """(enum, member) pairs used as <owner>.<Enum>.<MEMBER> in the given files."""
    uses = set()
    for p in paths:
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and \
                    isinstance(node.value.value, ast.Name) and node.value.value.id in owner_names and \
                    node.value.attr in enum_names:
                uses.add((node.value.attr, node.attr))
    return uses


@pytest.mark.req("IF-001", "IF-012")
def test_tc_if_001_01_every_name_used_is_in_the_icd():
    """Every command, EVENT, DATA flag/status bit, FAULT, BLOCK, IO and sys_flags name the code uses
    (``pg.<Enum>.<NAME>``) exists in the ICD tables of the oracle ``ref_codec``."""
    # Verifies: IF-001, IF-012
    tables = {"Cmd": set(rc.CMD), "Event": set(rc.EVENT), "DataFlags": set(rc.DATA_FLAGS),
              "DataStatus": set(rc.DATA_STATUS), "Faults": set(rc.FAULTS), "Block": set(rc.BLOCK),
              "IoBits": set(rc.IO), "SysFlags": set(rc.SYS_FLAGS), "Status": set(rc.STATUS),
              "Features": set(rc.FEATURES), "AsyncType": set(rc.ASYNC), "StopCause": set(rc.STOP_CAUSE),
              "ResetCause": set(rc.RESET_CAUSE), "Source": set(rc.SOURCE)}
    uses = _attr_uses(_modules("core", "io", "calc", "gui") + [SRC / "__main__.py"], {"pg", "protocol_gen"},
                      set(tables))
    assert len(uses) > 40          # sanity floor of the scan (v0.3: B uses more local aliases; was > 60)
    retired = _retired_icd_names()
    missing = sorted(f"{e}.{m}" for e, m in uses if m not in tables[e] and m not in retired)
    assert not missing, missing


def _retired_icd_names() -> set[str]:
    """Names withdrawn in the ICD (``retired:`` in protocol.yaml, ICD v0.5 CR-01: STOP_BTN, STOP_BUTTON) — oracle =
    the Integrator's single source, read directly (never protocol_gen)."""
    import yaml

    doc = yaml.safe_load((REPO / "00_System" / "specs" / "protocol.yaml").read_text(encoding="utf-8"))
    return {it["name"] for t in doc.get("tables", []) for it in t.get("items", []) if it.get("retired")}


@pytest.mark.req("IF-001", "SAF-SW-005")
def test_tc_if_001_02_no_retired_icd_name_in_backend_or_gui():
    """CR-01 / D-36 (ICD v0.5): no backend / GUI module (simulator excluded) uses a retired ICD name — STOP_BTN /
    STOP_BUTTON are reserved, never sent, and must not drive a gate, indicator or text."""
    # Verifies: IF-001, SAF-SW-005
    retired = _retired_icd_names()
    assert {"STOP_BTN", "STOP_BUTTON"} <= retired
    files = [p for p in _modules("core", "io", "calc", "gui") if "sim" not in p.relative_to(SRC).parts]
    used = sorted(f"{p.relative_to(SRC)}:{e}.{m}" for p in files
                  for e, m in _attr_uses([p], {"pg", "protocol_gen"}, {"DataStatus", "IoBits", "Event", "StopCause"})
                  if m in retired)
    assert not used, used


_CR01_TEXT = re.compile(r"physical STOP|STOP/BREAK|(?<![Ee]-)STOP (?:input|button (?:active|pressed|released))",
                        re.I)                                  # "E-stop input / button" is the red E-stop: allowed


def _user_strings(path: Path) -> list[tuple[int, str]]:
    """String literals of a module except docstrings (module / class / function) and comments."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            doc_ids.add(id(node.body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_ids]


@pytest.mark.req("SAF-SW-005", "SW-STOP-002")
def test_tc_saf_sw_005_04_no_text_refers_to_a_physical_stop_button():
    """CR-01 / D-36: the only physical stop is the red E-stop (power cut); no operator text (indicator, gate, clear
    hint, alarm) may point to a physical STOP/BREAK button or STOP input (KL-07). Docstrings / comments excluded."""
    # Verifies: SAF-SW-005, SW-STOP-002
    hits = [f"{p.relative_to(SRC)}:{ln}: {s[:80]!r}" for p in _modules("core", "gui")
            for ln, s in _user_strings(p) if _CR01_TEXT.search(s)]
    assert not hits, hits


@pytest.mark.req("IF-001", "IF-010")
def test_tc_if_001_01_no_hand_written_code_tables_no_oracle_imports():
    """No production module imports the oracles (ref_codec / ref_cmdcheck) and none re-defines the protocol name
    tables (Cmd / Event / Status enums or NAME = 0x.. command constants) outside the generated module."""
    # Verifies: IF-001, IF-010
    bad = []
    names = set(rc.CMD) | set(rc.EVENT)
    for p in _modules("core", "io", "calc", "gui"):
        src = p.read_text(encoding="utf-8")
        if re.search(r"^\s*(import|from)\s+(ref_codec|ref_cmdcheck)\b", src, re.M):
            bad.append(f"{p.name}: imports an oracle")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ClassDef) and node.name in ("Cmd", "Event", "Status", "Block", "DataFlags"):
                bad.append(f"{p.name}: class {node.name}")
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and \
                    isinstance(node.value.value, int) and not isinstance(node.value.value, bool):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in names:
                        bad.append(f"{p.name}: {t.id} = {node.value.value}")
    assert not bad, bad


# ============================================================================================ IF-002

@pytest.mark.req("IF-002")
def test_tc_if_002_01_serial_settings_once_and_no_port_opened(monkeypatch):
    """SerialTransport opens pyserial with 921 600 Bd, 8N1, no xonxoff / rtscts / dsrdtr and a read timeout set
    once; read() never re-configures the timeout; endpoints() opens nothing (D-06)."""
    # Verifies: IF-002
    import serial

    calls, sets = [], []

    class FakeSerial:
        def __init__(self, *a, **k):
            calls.append((a, k))
            self.is_open = True
            self.in_waiting = 0

        def __setattr__(self, name, value):
            if name in ("timeout", "baudrate", "bytesize", "parity", "stopbits"):
                sets.append(name)
            object.__setattr__(self, name, value)

        def read(self, n=1):
            return b""

        def write(self, d):
            return len(d)

        def close(self):
            pass

    monkeypatch.setattr(serial, "Serial", FakeSerial)
    from bend_stand.io.transport import transport_factory

    tr = transport_factory("COM7")
    assert not calls                                                   # nothing opened by the factory
    tr.open()
    (args, kw), = calls
    params = dict(zip(("port", "baudrate"), args)) | kw
    assert params["port"] == "COM7" and params["baudrate"] == 921_600
    assert params.get("bytesize", 8) == 8 and params.get("parity", "N") == "N" and params.get("stopbits", 1) == 1
    assert not params.get("xonxoff") and not params.get("rtscts") and not params.get("dsrdtr")
    assert params.get("timeout") is not None
    n_sets = len(sets)
    for _ in range(50):
        tr.read(4096, 0.002)
    tr.write(b"\xa5\x5a")
    assert len(sets) == n_sets                                         # no per-read SetCommState
    tr.close()
    from bend_stand.core.backend import Backend

    calls.clear()
    eps = Backend().endpoints()
    assert any(e.endpoint == "sim" for e in eps) and not calls


# ============================================================================================ IF-010

@pytest.mark.req("IF-010")
@pytest.mark.parametrize("tool", ["gen_params.py", "gen_vectors.py"])
def test_tc_if_010_01_generators_check(tool):
    """Generated code and vectors are up to date (`--check` exit 0, no writes)."""
    # Verifies: IF-010
    r = subprocess.run([sys.executable, str(TOOLS / tool), "--check"], cwd=str(REPO), capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-2000:]


@pytest.mark.req("IF-010")
def test_tc_if_010_01_dictionary_hash_matches(pdict):
    """params_gen.PARAM_DICT_HASH == `gen_params.py --hash` == the hash of the loaded params.yaml."""
    # Verifies: IF-010
    from bend_stand.core import params_gen as pgen

    r = subprocess.run([sys.executable, str(TOOLS / "gen_params.py"), "--hash"], cwd=str(REPO), capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0
    h = int(re.search(r"0x[0-9A-Fa-f]{8}", r.stdout).group(0), 16)
    assert h == pgen.PARAM_DICT_HASH == pdict.hash   # ICD v0.5: dict_version 4 (47 params); no literal (oracle = params.yaml)


# ============================================================================================ SYS-010

ORIGIN = re.compile(r"Origin:[^\n]*?(Thrust_Stand_HAW|Stefan)/\S+\s*@([0-9a-f]{7,40})")


def _src_files(sub: str | None = None) -> list[Path]:
    base = SRC / sub if sub else SRC
    files = [p for p in base.rglob("*.py") if p.name not in GENERATED and p.name != "__init__.py"]
    if sub is None:
        files += [SRC / "__init__.py"]
    return files


@pytest.mark.req("SYS-010")
def test_tc_sys_010_01_origin_notes():
    """Every SW source file that cites Thrust_Stand_HAW / Stefan as its origin carries
    'Origin: … <repo>/<path> @<hash>' (path and commit hash, D-29 m)."""
    # Verifies: SYS-010
    bad = []
    for p in _src_files():
        s = p.read_text(encoding="utf-8")
        if ("Thrust_Stand_HAW" in s or "Stefan/" in s) and "Origin" in s and not ORIGIN.search(s):
            bad.append(p.relative_to(SRC).as_posix())
    assert not bad, bad


@pytest.mark.req("SYS-010")
@pytest.mark.parametrize("sub", ["core", "io", "calc"])
def test_tc_sys_010_01_implements_tags_backend(sub):
    """Every hand-written backend module carries an 'Implements:' tag (CLAUDE.md convention, SYS-010)."""
    # Verifies: SYS-010
    bad = [p.relative_to(SRC).as_posix() for p in _src_files(sub) if "Implements:" not in p.read_text(encoding="utf-8")]
    assert not bad, bad


@pytest.mark.req("SYS-010")
@pytest.mark.defect("SWD-M1-07")
def test_tc_sys_010_01_implements_tags_gui():
    """Every hand-written GUI module carries an 'Implements:' tag."""
    # Verifies: SYS-010
    bad = [p.relative_to(SRC).as_posix() for p in _src_files("gui") if "Implements:" not in p.read_text(encoding="utf-8")]
    assert not bad, bad


@pytest.mark.req("SYS-010")
def test_tc_sys_010_01_reuse_mentions_have_an_origin_line():
    """A file that says it was copied/adapted from Thrust_Stand_HAW must have the origin line (not only a
    mention in prose)."""
    # Verifies: SYS-010
    bad = []
    for p in SRC.rglob("*.py"):
        s = p.read_text(encoding="utf-8")
        if re.search(r"(copied|copy|adapted|Origin)[^\n]{0,80}Thrust_Stand_HAW", s) and not ORIGIN.search(s):
            bad.append(p.relative_to(SRC).as_posix())
    assert not bad, bad


# ============================================================================================ SW-PLT-001

@pytest.mark.req("SW-PLT-001")
def test_tc_sw_plt_001_01_runtime_and_requirements_inspection():
    """SW-PLT-001 (inspection part): Python 3.14 64-bit runtime; requirements.txt lists exactly PySide6
    (Essentials), pyqtgraph, numpy, pyserial pinned; requirements-dev.txt includes it plus the test tools."""
    # Verifies: SW-PLT-001
    assert sys.version_info[:2] == (3, 14) and sys.maxsize > 2**32
    req = [ln.split("==")[0].strip().lower() for ln in (SW / "requirements.txt").read_text(encoding="utf-8")
           .splitlines() if ln.strip() and not ln.startswith("#")]
    assert sorted(req) == sorted(["pyside6-essentials", "pyqtgraph", "numpy", "pyserial"])
    dev = (SW / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev and all(t in dev for t in ("pytest==", "pytest-qt==", "pytest-cov==",
                                                                    "pytest-randomly=="))
    import importlib.metadata as md

    for name, ver in [ln.split("==") for ln in (SW / "requirements.txt").read_text(encoding="utf-8").splitlines()
                      if "==" in ln and not ln.startswith("#")]:
        assert md.version(name.strip()) == ver.strip(), name
    _ = math


_SEEN_DATA_DIRS: list[str] = []


@pytest.mark.req("SW-PLT-001", "SW-CAL-004")
@pytest.mark.parametrize("run", [1, 2])
def test_tc_sw_plt_001_02_private_data_dir_per_test(run, tmp_path):
    """OBS-D-M3-01 (B's root conftest): every test runs with its own ``BEND_STAND_DATA_DIR`` (never %APPDATA%), the
    backend resolves its data folder there, and two tests never share it (calibration / session files cannot leak
    between tests → order independence)."""
    # Verifies: SW-PLT-001, SW-CAL-004
    import os

    from bend_stand.core.paths import app_data_dir

    d = os.environ.get("BEND_STAND_DATA_DIR")
    assert d, "BEND_STAND_DATA_DIR not set for this test"
    appdata = os.environ.get("APPDATA", "")
    assert not (appdata and os.path.normcase(os.path.abspath(d)).startswith(os.path.normcase(os.path.abspath(appdata))))
    assert os.path.normcase(str(app_data_dir())) == os.path.normcase(os.path.abspath(d))
    assert d not in _SEEN_DATA_DIRS
    _SEEN_DATA_DIRS.append(d)

"""Bring-up assistant engine (D-56 a, SRS SW-BRU-001…004; SW_design §25). Qt-free.

* **Step definitions** — data file ``bend_stand/resources/bringup_steps.json`` (schema
  ``bringup_steps.schema.json``, ``bird.bend.bringup_steps`` v1), authored from the delivered bring-up guide (same
  step ids). :func:`load_definitions` validates the schema and the semantics (unique ids, known sections, action ids,
  condition sources / keys / ops, kind parameters).
* **Session** — ``<data>/bringup/sessions/<id>/``: ``session.json`` (atomic, after every change), ``events.jsonl``
  (every EventBus record, DATA flag / status changes, STATUS changes; appended + flushed), ``params/`` (full board
  parameter set at the start, after every NVM save, at the end and at every package), ``info.json``,
  ``snapshots.jsonl`` (link statistics, NVM state), ``settings_start.json``, ``steps_used.json``. The active session
  is resumed after an application / PC restart (``<data>/bringup/active.json``).
* **Evaluation** — automatic results from the backend status / events (``auto.stages`` of conditions, evaluated on
  the Supervisor tick while the step is selected); operator entries (confirmation, measured values with limits, text,
  choice); wizard results from ``cal.travel.state`` / ``cal.load.state`` / ``tare.state``; the pulse-integrity check
  (D-56 b, SYS-011) as the only engine-run action.
* **SW-BRU-004** — the engine never moves the axis by itself: GUI actions run through the normal GUI handlers, and
  the pulse-integrity check runs only from an explicit, confirmed ``run_action`` and uses ``motion.move_to`` /
  ``motion.home`` (every gate, every move); a refusal is returned unchanged.
* **Package** (SW-BRU-003) — :func:`build_package`: one zip with ``report.html``, ``bringup.json`` (validated
  against ``bringup_record.schema.json``), the collected files and ``manifest.json`` (SHA-256 of every file); only
  files below the data folder and the recordings roots of the session; offline (CLI ``bringup-export``).

Implements: SW-BRU-001, SW-BRU-002, SW-BRU-003, SW-BRU-004, SYS-011 (pulse-integrity check, D-56 b)
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as _dt
import enum
import hashlib
import html
import json
import logging
import math
import os
import platform
import re
import sys
import tempfile
import threading
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bend_stand.core import minischema, paths
from bend_stand.core import protocol_gen as pg
from bend_stand.core.errors import ConfirmationRequired, FileFormatError, GateRefused, NackError
from bend_stand.core.model import (
    GATE_OK, INDICATOR_KEYS, GateItem, GateResult, Issue, IssueSeverity, LinkState, MotionKind, Severity,
)
from bend_stand.core.schema import atomic_write_json, parse_json_text, read_json

log = logging.getLogger("bend_stand.core.bringup")
MS = 1_000_000

STEPS_KIND, STEPS_VERSION = "bringup_steps", 1
SESSION_KIND, SESSION_VERSION = "bringup_session", 1
RECORD_KIND, RECORD_VERSION = "bringup_record", 1
MANIFEST_KIND, MANIFEST_VERSION = "bringup_manifest", 1
STEPS_FILE = "bringup_steps.json"
STEPS_SCHEMA_FILE = "bringup_steps.schema.json"
RECORD_SCHEMA_FILE = "bringup_record.schema.json"
BRINGUP_ENV = "BEND_STAND_BRINGUP_DIR"          # package output folder override (tests, portable use)

STATES = ("NOT_DONE", "PASS", "FAIL", "SKIPPED")
#: who may set a step state with ``mark`` (§25.2): the operator in the dock, or the headless simulator smoke test
#: (``--headless --sim --bringup-mark``) — shown in session.json, bringup.json and report.html
MARK_ORIGINS = ("operator", "cli-test")
KINDS = ("info", "confirm", "measure", "auto", "motion", "wizard", "nvm", "pulse_integrity")
#: GUI action ids (``gui/widgets/bringup_dock.ACTIONS``) — run by the normal GUI handlers (SW-BRU-004)
GUI_ACTIONS = ("enable", "disable", "home", "no_specimen", "leave_no_specimen", "travel_wizard", "load_wizard",
               "tare", "save_nvm", "save_file", "read_all", "clear_stop", "hotkey_test")
SHOW_TABS = ("connection", "limits", "marks", "manual", "calibration")
#: actions run by this engine (``run_action``): the GUI forwards the operator's click (with the CONFIRM dialog)
ENGINE_ACTIONS = ("pulse_integrity",)
WIZARD_OF_ACTION = {"travel_wizard": "travel_cal", "load_wizard": "load_cal", "tare": "tare"}
WIZARD_TOPIC = {"travel_cal": "cal.travel.state", "load_cal": "cal.load.state", "tare": "tare.state"}
MOTION_ACTIONS = frozenset({"home", "travel_wizard", "pulse_integrity"})
COND_SRC = ("ind", "data_flag", "data_status", "fault", "io", "sys_flag", "backend", "link", "stats", "stream",
            "motion", "safety", "calibration", "tare", "recording", "info", "param", "event", "topic", "nvm", "step")
COND_OPS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "contains", "regex", "between", "present", "absent",
            "set", "clear", "on", "off", "seen", "not_seen")
INFO_KEYS = ("fw_version", "proto", "proto_major", "proto_minor", "payload_version", "param_dict_hash", "uid",
             "build", "param_count", "features", "feature_mask")
NVM_KEYS = ("saves", "matches_last_save", "record_seq", "dumps")
#: pulse-integrity parameters (D-56 b, E's review, SYS-011) — the numbers live in the step file, not here
PULSE_PARAMS = ("baseline_homes", "baseline_max_um", "round_trips", "from_mm", "to_mm", "speed_mm_s", "d_floor_um",
                "d_factor", "repeats_allowed", "home_event_timeout_s", "fail_text", "baseline_fail_text")
#: FW events between the baseline and the final re-home (E's review (b) + B-BRU-04 item 2): ``ABORT`` = readings
#: discarded, run again from the baseline; ``FAIL`` = step FAIL, stop the bring-up and report. ``ALM_CHANGED`` counts
#: only when the alarm becomes active; ``LIMIT_SET`` only during a move (homing uses the START switch).
PULSE_DISTURB: dict[str, tuple[str, str, str]] = {
    "ESTOP_SET": ("ABORT", "E-stop", ""), "DRIVER_DISABLED": ("ABORT", "driver disabled", ""),
    "HOME_FAILED": ("ABORT", "homing failed", ""), "LINK_WDG": ("ABORT", "link watchdog", ""),
    "HALT_SET": ("ABORT", "HALT", ""), "PAUSED": ("ABORT", "PAUSE", ""), "ALM_CHANGED": ("ABORT", "driver alarm (ALM)", ""),
    "BOOT": ("FAIL", "unexpected board restart (BOOT)", "BOOT"),
    "LIMIT_SET": ("FAIL", "limit switch reached during a move", "LIMIT"),
}
#: FAULT_SET args that make the pulse-integrity step FAIL (stop + report); other faults abort the run
PULSE_FAIL_FAULTS = frozenset({"STEP_FAULT", "LOAD_LIMIT", "AFE_FAULT"})
#: board data (DATA or GET_STATUS) younger than this confirms HOMED before a re-home (B-BRU-04 item 8)
FRESH_NS = 300 * MS
#: a BOOT this soon after a run aborted by a link loss turns that attempt into the BOOT FAIL
BOOT_AFTER_ABORT_NS = 10_000 * MS
EVAL_PERIOD_NS = 100 * MS
SNAPSHOT_PERIOD_NS = 60_000 * MS
DRAIN_PERIOD_NS = 100 * MS
MAX_QUEUE = 200_000
SKIP_TOPICS = frozenset({"bringup.state", "bringup.step"})


# ============================================================================================ GUI-facing types
# Field names follow the GUI contract GRQ-B-40 (tests/gui/fake_bringup.py, SW_design_GUI §3.8); extras last.

@dataclass(frozen=True)
class BuField:
    name: str
    label: str
    kind: str = "number"            # confirm | number | text | choice
    unit: str = ""
    min: float | None = None
    max: float | None = None
    decimals: int = 2
    choices: tuple[str, ...] = ()
    required: bool = True
    pass_values: tuple[Any, ...] = ()
    #: required only while one of these entry conditions holds (``{step?, field, op, value}``; any-of)
    required_when: Any = None
    #: entry rules: ``{values?, when, text}`` — the entered value (or one of ``values``) is refused (ERROR
    #: ``INVALID``, ``text``) while one of the ``when`` entry conditions holds (§25.1)
    invalid_when: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class BuAuto:
    state: str = "NA"               # WAITING | PASS | FAIL | NA
    text: str = ""


@dataclass(frozen=True)
class BuStep:
    id: str
    section: str
    title: str
    instruction: str
    expected: str
    kind: str = "operator"          # auto | operator | action | info (GUI display kind)
    fields: tuple[BuField, ...] = ()
    action: str | None = None
    motion: bool = False
    stop_if_fail: bool = False
    safety: str = ""
    hg: str = ""
    auto: BuAuto | None = None
    state: str = "NOT_DONE"         # NOT_DONE | PASS | FAIL | SKIPPED
    entries: Mapping[str, Any] = field(default_factory=dict)
    remark: str = ""
    skip_reason: str = ""
    # extras (B)
    step_kind: str = "info"         # kind of the step file (KINDS)
    optional: bool = False
    engine_action: bool = False     # True: the GUI calls ``bringup.run_action(id, confirmed=…)``
    action_state: str | None = None     # engine / wizard action: RUNNING | PASS | FAIL | ABORTED
    action_text: str = ""
    manual: bool = False            # state set by the operator (mark), not by the evaluation
    origin: str = ""                # who set the state: auto | operator | auto+operator | cli-test ("" = not done)


@dataclass(frozen=True)
class BuSection:
    id: str
    title: str
    step_ids: tuple[str, ...]


@dataclass(frozen=True)
class BuStatus:
    active: bool = False
    session_id: str | None = None
    board_uid: str | None = None
    fw_build: str | None = None
    started_utc: str | None = None
    total: int = 0
    done: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    current: str | None = None
    folder: str | None = None
    # extras (B)
    closed_utc: str | None = None
    action_running: str | None = None
    last_package: str | None = None
    definitions: str = ""
    board_mismatch: str | None = None   # SWD-P3-04: another board is connected — the session refuses to continue


@dataclass(frozen=True)
class BuPackage:
    path: str
    size_bytes: int
    files: int
    send_to: str
    partial: bool = True
    sha256: str = ""


# ============================================================================================ helpers

def utc_iso(wall_ns: int | None = None) -> str:
    t = _dt.datetime.now(_dt.UTC) if wall_ns is None else _dt.datetime.fromtimestamp(wall_ns / 1e9, tz=_dt.UTC)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def resource_path(name: str) -> Path:
    """A file of ``bend_stand/resources`` (source tree, wheel, frozen build)."""
    return Path(__file__).resolve().parent.parent / "resources" / name


def default_bringup_root() -> Path:
    """Package output folder: env ``BEND_STAND_BRINGUP_DIR``, else ``Documents/BirdBendStand/bringup``."""
    env = os.environ.get(BRINGUP_ENV, "").strip()
    return Path(env) if env else paths.default_recordings_root().parent / "bringup"


def sessions_root(data_dir: Path) -> Path:
    return Path(data_dir) / "bringup" / "sessions"


def jsonable(obj: Any, depth: int = 0) -> Any:
    """Frozen dataclasses / enums / mappings / tuples → JSON values (NaN / inf → None)."""
    if depth > 12:
        return repr(obj)
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, enum.Enum):
        v = obj.value
        return v if isinstance(v, (str, int, float)) and not isinstance(v, bool) else str(v)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name), depth + 1) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        return {str(k.value if isinstance(k, enum.Enum) else k): jsonable(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = sorted(obj, key=str) if isinstance(obj, (set, frozenset)) else obj
        return [jsonable(v, depth + 1) for v in items]
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__dict__"):
        return {k: jsonable(v, depth + 1) for k, v in vars(obj).items() if not k.startswith("_")}
    try:
        f = float(obj)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return repr(obj)


def _bits(table: Sequence[str], value: int | None) -> list[str]:
    if value is None:
        return []
    return [n for i, n in enumerate(table) if n and value >> i & 1]


def _safe_name(s: str | None, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip()).strip("-.")
    return s or fallback


def _constants() -> dict[str, Any]:
    from bend_stand import ICD_VERSION, PARAM_DICT_HASH, PAYLOAD_VERSION, PROTO_MAJOR, PROTO_MINOR, __version__

    return {"PARAM_DICT_HASH": PARAM_DICT_HASH, "PROTO_MAJOR": PROTO_MAJOR, "PROTO_MINOR": PROTO_MINOR,
            "PAYLOAD_VERSION": PAYLOAD_VERSION, "ICD_VERSION": ICD_VERSION, "SW_VERSION": __version__}


def versions(info: Any = None) -> dict[str, Any]:
    """App / FW / OS versions for the package (SW-BRU-003)."""
    c = _constants()
    d: dict[str, Any] = {"sw_version": c["SW_VERSION"], "icd_version": c["ICD_VERSION"],
                         "proto": f"{c['PROTO_MAJOR']}.{c['PROTO_MINOR']}", "payload_version": c["PAYLOAD_VERSION"],
                         "param_dict_hash": f"0x{c['PARAM_DICT_HASH']:08X}", "python": sys.version.split()[0],
                         "os": platform.platform(), "frozen": bool(getattr(sys, "frozen", False))}
    if info is not None:
        d.update(fw_version=".".join(map(str, info.fw_version)), fw_build=info.build, board_uid=info.uid,
                 fw_proto=f"{info.proto_major}.{info.proto_minor}",
                 fw_param_dict_hash=f"0x{int(info.param_dict_hash):08X}")
    return d


# ============================================================================================ definitions

@dataclass(frozen=True)
class StepDef:
    id: str
    section: str
    title: str
    instruction: str
    expected: str
    kind: str
    fields: tuple[BuField, ...] = ()
    auto: Mapping[str, Any] | None = None
    action: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    optional: bool = False
    stop_if_fail: bool = False
    safety: str = ""
    hg: str = ""
    motion: bool = False

    @property
    def wizard(self) -> str | None:
        return self.params.get("wizard") or WIZARD_OF_ACTION.get(self.action or "")

    @property
    def engine_action(self) -> bool:
        return self.action in ENGINE_ACTIONS

    @property
    def display_kind(self) -> str:
        if self.action:
            return "action"
        if self.auto is not None:
            return "auto"
        if self.fields:
            return "operator"
        return "info"


@dataclass(frozen=True)
class Definitions:
    guide: Mapping[str, Any]
    send_to: str
    sections: tuple[tuple[str, str], ...]
    steps: tuple[StepDef, ...]
    sha256: str
    source: str
    raw: Mapping[str, Any]

    def step(self, step_id: str) -> StepDef:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.steps)


def _cond_problems(c: Mapping[str, Any], where: str) -> list[str]:
    src, key, op = c.get("src"), str(c.get("key", "")), c.get("op")
    out: list[str] = []
    tables = {"data_flag": pg.DATA_FLAGS_BITS, "data_status": pg.DATA_STATUS_BITS, "fault": pg.FAULTS_BITS,
              "io": pg.IO_BITS, "sys_flag": pg.SYS_FLAGS_BITS}
    if src in tables and key not in [n for n in tables[src] if n]:
        out.append(f"{where}: unknown {src} bit {key!r}")
    elif src == "ind" and key not in INDICATOR_KEYS:
        out.append(f"{where}: unknown indicator {key!r}")
    elif src == "info" and key not in INFO_KEYS:
        out.append(f"{where}: unknown info key {key!r}")
    elif src == "event" and key not in pg.EVENT_NAMES:
        out.append(f"{where}: unknown FW event {key!r}")
    elif src == "nvm" and key not in NVM_KEYS:
        out.append(f"{where}: unknown nvm key {key!r}")
    elif src == "param":
        from bend_stand.core import params_gen  # noqa: PLC0415

        if key not in {m.key for m in params_gen.PARAMS}:
            out.append(f"{where}: unknown parameter {key!r}")
    if c.get("path") is not None and src != "step":
        out.append(f"{where}: path only for src step")
    if op in ("seen", "not_seen") and src not in ("event", "topic"):
        out.append(f"{where}: op {op} only for src event / topic")
    if op in ("between",) and not (isinstance(c.get("value"), list) and len(c["value"]) == 2):
        out.append(f"{where}: between needs [lo, hi]")
    if op in ("in", "not_in") and not isinstance(c.get("value"), list):
        out.append(f"{where}: {op} needs a list value")
    v = c.get("value")
    if isinstance(v, Mapping) and not ({"$const"} == set(v) and v["$const"] in _constants()
                                       or {"$param"} == set(v)):
        out.append(f"{where}: bad value reference {v!r}")
    return out


def check_definitions(d: Mapping[str, Any]) -> list[str]:
    """Schema + semantic problems of a step-definition document (empty = valid)."""
    try:
        schema = json.loads(resource_path(STEPS_SCHEMA_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - shipped file
        return [f"schema not readable: {exc}"]
    errs = minischema.validate(d, schema)
    if errs:
        return errs
    sec_ids = [s["id"] for s in d["sections"]]
    steps_by_id = {s["id"]: s for s in d["steps"]}
    if len(set(sec_ids)) != len(sec_ids):
        errs.append("duplicate section ids")
    seen: set[str] = set()
    for i, s in enumerate(d["steps"]):
        w = f"step {s.get('id', i)}"
        if s["id"] in seen:
            errs.append(f"{w}: duplicate id")
        seen.add(s["id"])
        if s["section"] not in sec_ids:
            errs.append(f"{w}: unknown section {s['section']!r}")
        act = s.get("action")
        if act and not (act in GUI_ACTIONS or act in ENGINE_ACTIONS
                        or (act.startswith("show:") and act[5:] in SHOW_TABS)):
            errs.append(f"{w}: unknown action {act!r}")
        kind, params = s["kind"], s.get("params", {})
        if kind == "pulse_integrity":
            if act != "pulse_integrity":
                errs.append(f"{w}: kind pulse_integrity needs action 'pulse_integrity'")
            miss = [k for k in PULSE_PARAMS if k not in params]
            if miss:
                errs.append(f"{w}: pulse_integrity params missing {miss}")
            else:
                for k in ("baseline_homes", "round_trips"):
                    if not (isinstance(params[k], int) and 1 <= params[k] <= 100):
                        errs.append(f"{w}: {k} must be an integer 1…100")
                if not (isinstance(params["repeats_allowed"], int) and 0 <= params["repeats_allowed"] <= 5):
                    errs.append(f"{w}: repeats_allowed must be an integer 0…5")
                for k in ("baseline_max_um", "from_mm", "to_mm", "d_floor_um", "d_factor", "home_event_timeout_s"):
                    if not isinstance(params[k], (int, float)) or isinstance(params[k], bool):
                        errs.append(f"{w}: {k} must be a number")
                sp = params["speed_mm_s"]
                if not (isinstance(sp, (int, float)) and not isinstance(sp, bool) and sp > 0):
                    errs.append(f"{w}: speed_mm_s must be a number > 0 (the qualification speed, B-BRU-04 item 1)")
        elif act == "pulse_integrity":
            errs.append(f"{w}: action pulse_integrity needs kind pulse_integrity")
        if kind == "wizard":
            wz = params.get("wizard") or WIZARD_OF_ACTION.get(act or "")
            if wz not in WIZARD_TOPIC:
                errs.append(f"{w}: wizard step needs params.wizard (travel_cal | load_cal | tare) or a wizard action")
        if kind == "measure" and not any(f["kind"] == "number" for f in s.get("fields", ())):
            errs.append(f"{w}: measure step needs a number field")
        if kind == "auto" and "auto" not in s:
            errs.append(f"{w}: auto step needs 'auto'")
        names = [f["name"] for f in s.get("fields", ())]
        if len(set(names)) != len(names):
            errs.append(f"{w}: duplicate field names")
        for f in s.get("fields", ()):
            if f["kind"] == "choice" and not f.get("choices"):
                errs.append(f"{w}: choice field {f['name']} without choices")
            refs = [("required_when", rc) for rc in _as_list(f.get("required_when"))]
            for rule in f.get("invalid_when", ()):
                refs += [("invalid_when", rc) for rc in _as_list(rule.get("when"))]
            for what, rc in refs:
                other = rc.get("step")
                if other is None:
                    if rc["field"] not in names or rc["field"] == f["name"]:
                        errs.append(f"{w}: field {f['name']} {what} refers to {rc['field']!r}")
                else:
                    o = steps_by_id.get(other)
                    if o is None or other == s["id"]:
                        errs.append(f"{w}: field {f['name']} {what} refers to step {other!r}")
                    elif rc["field"] not in {x["name"] for x in o.get("fields", ())}:
                        errs.append(f"{w}: field {f['name']} {what} refers to {other}.{rc['field']}")
                if rc["op"] in ("seen", "not_seen", "on", "off"):
                    errs.append(f"{w}: field {f['name']} {what} op {rc['op']} not allowed")
            lo, hi = f.get("min"), f.get("max")
            if lo is not None and hi is not None and lo > hi:
                errs.append(f"{w}: field {f['name']} min > max")
        a = s.get("auto")
        if a:
            for j, st in enumerate(a["stages"]):
                for k, c in enumerate(st["all"]):
                    errs += _cond_problems(c, f"{w} stage {j + 1} condition {k + 1}")
            for k, c in enumerate(a.get("fail_if", ())):
                errs += _cond_problems(c, f"{w} fail_if {k + 1}")
                if c.get("op") == "not_seen":
                    errs.append(f"{w} fail_if {k + 1}: not_seen cannot fail a step")
    all_ids = {s["id"] for s in d["steps"]}
    for s in d["steps"]:
        a = s.get("auto")
        conds = [] if not a else [c for st in a["stages"] for c in st["all"]] + list(a.get("fail_if", ()))
        for c in conds:
            if c.get("src") == "step" and (c["key"] not in all_ids or c["key"] == s["id"]):
                errs.append(f"step {s['id']}: step condition refers to {c['key']!r}")
    return errs


def _step_from(d: Mapping[str, Any]) -> StepDef:
    fields_ = tuple(BuField(f["name"], f["label"], f["kind"], f.get("unit", ""), f.get("min"), f.get("max"),
                            int(f.get("decimals", 2)), tuple(f.get("choices", ())), bool(f.get("required", True)),
                            tuple(f.get("pass_values", ())), f.get("required_when"),
                            tuple(f.get("invalid_when", ())))
                    for f in d.get("fields", ()))
    kind = d["kind"]
    action = d.get("action")
    motion = d.get("motion")
    if motion is None:
        motion = kind in ("motion", "pulse_integrity") or action in MOTION_ACTIONS
    return StepDef(d["id"], d["section"], d["title"], d["instruction"], d.get("expected", ""), kind, fields_,
                   d.get("auto"), action, dict(d.get("params", {})), bool(d.get("optional", False)),
                   bool(d.get("stop_if_fail", False)), d.get("safety", ""), d.get("hg", ""), bool(motion))


def parse_definitions(text: str, source: str) -> Definitions:
    d = parse_json_text(text, source)
    if not isinstance(d, dict) or d.get("schema") != f"bird.bend.{STEPS_KIND}":
        raise FileFormatError(f"{source}: not a bird.bend.{STEPS_KIND} file")
    if isinstance(d.get("schema_version"), int) and d["schema_version"] > STEPS_VERSION:
        raise FileFormatError(f"{source}: made by a newer SW version (schema_version {d['schema_version']})")
    errs = check_definitions(d)
    if errs:
        raise FileFormatError(f"{source}: " + "; ".join(errs[:10]))
    return Definitions(dict(d["guide"]), str(d.get("send_to", "Send this file to the product owner.")),
                       tuple((s["id"], s["title"]) for s in d["sections"]), tuple(_step_from(s) for s in d["steps"]),
                       hashlib.sha256(text.encode("utf-8")).hexdigest(), source, d)


def load_definitions(path: str | os.PathLike[str] | None = None) -> Definitions:
    """The shipped step file (or ``path``); ``FileFormatError`` on any problem."""
    p = Path(path) if path else resource_path(STEPS_FILE)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileFormatError(f"cannot read {p}: {exc}") from exc
    return parse_definitions(text, str(p))


# ============================================================================================ conditions (pure)

@dataclass
class EvalContext:
    """What the conditions see: the backend status, DATA bits, board values, events since the stage was armed."""

    status: Any = None
    flags: int = 0
    data_status: int = 0
    params: Mapping[str, Any] = field(default_factory=dict)
    events: list[Any] = field(default_factory=list)        # FwEvent-like (name, arg, arg_name, value)
    topics: set[str] = field(default_factory=set)
    nvm: Mapping[str, Any] = field(default_factory=dict)
    constants: Mapping[str, Any] = field(default_factory=dict)
    steps: Mapping[str, Any] = field(default_factory=dict)  # step records of the session (src "step")


def _path(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if obj is None:
            return None
        if isinstance(obj, Mapping):
            obj = obj.get(part)
        else:
            obj = getattr(obj, part, None)
    return obj


def _norm(v: Any) -> Any:
    if isinstance(v, enum.Enum):
        return v.value if isinstance(v.value, str) else int(v.value)
    if isinstance(v, (tuple, set, frozenset)):
        return sorted(v) if isinstance(v, (set, frozenset)) else list(v)
    return v


def _value_of(c: Mapping[str, Any], ctx: EvalContext) -> Any:
    src, key = c["src"], str(c["key"])
    st = ctx.status
    if src == "ind":
        ind = None if st is None else st.indicators.get(key)
        return None if ind is None else ind.state
    if src in ("data_flag", "data_status"):
        table = pg.DATA_FLAGS_BITS if src == "data_flag" else pg.DATA_STATUS_BITS
        word = ctx.flags if src == "data_flag" else ctx.data_status
        return bool(word >> table.index(key) & 1)
    if src in ("fault", "io", "sys_flag"):
        board = None if st is None else st.board
        if board is None:
            return None
        table, word = {"fault": (pg.FAULTS_BITS, board.faults), "io": (pg.IO_BITS, board.io),
                       "sys_flag": (pg.SYS_FLAGS_BITS, board.sys_flags)}[src]
        return bool(int(word) >> table.index(key) & 1)
    if src == "backend":
        return _norm(_path(st, key))
    if src in ("link", "stream", "motion", "safety", "calibration", "tare", "recording"):
        return _norm(_path(None if st is None else getattr(st, src), key))
    if src == "stats":
        return _norm(_path(None if st is None else st.link.stats, key))
    if src == "info":
        info = None if st is None else st.link.info
        if info is None:
            return None
        if key == "fw_version":
            return ".".join(map(str, info.fw_version))
        if key == "proto":
            return f"{info.proto_major}.{info.proto_minor}"
        if key == "features":
            return sorted(info.features)
        return _norm(getattr(info, key, None))
    if src == "param":
        return ctx.params.get(key)
    if src == "nvm":
        return ctx.nvm.get(key)
    if src == "step":
        rec = ctx.steps.get(key)
        return None if rec is None else _norm(_path(rec, str(c.get("path") or "state")))
    return None


def _ref(v: Any, ctx: EvalContext) -> Any:
    if isinstance(v, Mapping):
        if "$const" in v:
            return ctx.constants.get(str(v["$const"]))
        if "$param" in v:
            return ctx.params.get(str(v["$param"]))
    return v


def _num(a: Any) -> float | None:
    if isinstance(a, bool) or a is None:
        return None
    try:
        f = float(a)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _eq(a: Any, b: Any) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None and not isinstance(a, str) and not isinstance(b, str):
        return abs(fa - fb) <= 1e-9 * max(1.0, abs(fa), abs(fb))
    return a == b


def apply_op(op: str, a: Any, b: Any) -> bool:
    """Pure comparison of an observed value ``a`` with the condition value ``b``."""
    if op == "present":
        return a is not None
    if op == "absent":
        return a is None
    if op in ("set", "clear"):
        return a is not None and bool(a) == (op == "set")
    if op in ("on", "off"):
        return a == ("ON" if op == "on" else "OFF")
    if a is None:
        return False
    if op == "eq":
        return _eq(a, b)
    if op == "ne":
        return not _eq(a, b)
    if op in ("lt", "le", "gt", "ge"):
        fa, fb = _num(a), _num(b)
        if fa is None or fb is None:
            return False
        return {"lt": fa < fb, "le": fa <= fb, "gt": fa > fb, "ge": fa >= fb}[op]
    if op in ("in", "not_in"):
        hit = any(_eq(a, x) for x in (b or ()))
        return hit if op == "in" else not hit
    if op == "contains":
        return b in a if isinstance(a, (str, list, tuple)) else False
    if op == "regex":
        return re.search(str(b), str(a)) is not None
    if op == "between":
        fa = _num(a)
        return fa is not None and _num(b[0]) is not None and _num(b[1]) is not None and \
            float(b[0]) <= fa <= float(b[1])
    return False


def _event_matches(ev: Any, c: Mapping[str, Any]) -> bool:
    if getattr(ev, "name", None) != c["key"]:
        return False
    arg = c.get("arg")
    if arg is None:
        return True
    return arg == getattr(ev, "arg_name", None) or arg == getattr(ev, "arg", None)


def eval_condition(c: Mapping[str, Any], ctx: EvalContext) -> tuple[bool, Any]:
    """``(holds, observed value)`` of one condition (pure)."""
    op, src = c["op"], c["src"]
    b = _ref(c.get("value"), ctx)
    if src == "event":
        hits = [e for e in ctx.events if _event_matches(e, c)]
        if op == "seen":
            return bool(hits), (hits[-1].value if hits else None)
        if op == "not_seen":
            return not hits, len(hits)
        a = hits[-1].value if hits else None
        return apply_op(op, a, b), a
    if src == "topic":
        seen = c["key"] in ctx.topics
        return (seen if op == "seen" else not seen) if op in ("seen", "not_seen") else False, seen
    a = _value_of(c, ctx)
    return apply_op(op, a, b), a


def cond_text(c: Mapping[str, Any], observed: Any) -> str:
    label = c.get("label") or f"{c['src']}.{c['key']}"
    if c["op"] in ("seen", "not_seen") and c["src"] in ("event", "topic"):
        return f"{label} {c['op'].replace('_', ' ')}" + (f" ({observed})" if c["op"] == "seen" and
                                                         observed is not None else "")
    if isinstance(observed, float):
        observed = f"{observed:.6g}"
    if c["src"] == "info" and c["key"] == "param_dict_hash" and isinstance(observed, int):
        observed = f"0x{observed:08X}"
    return f"{label} = {observed}"


# ============================================================================================ entries (pure)

def _as_list(x: Any) -> list[Mapping[str, Any]]:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def entry_condition(rc: Mapping[str, Any], entries: Mapping[str, Any],
                    others: Mapping[str, Mapping[str, Any]] | None = None) -> bool:
    """One entry condition ``{step?, field, op, value}`` against this step's entries or another step's (pure)."""
    src = entries if rc.get("step") is None else (others or {}).get(str(rc["step"])) or {}
    return apply_op(str(rc["op"]), src.get(str(rc["field"])), rc.get("value"))


def check_entries(fields_: Sequence[BuField], entries: Mapping[str, Any],
                  others: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[list[Issue], str | None]:
    """Validate operator entries; returns (issues, result) with result PASS / FAIL / None (incomplete).
    ``others`` = entries of the other steps (step id → entries) for ``required_when`` / ``invalid_when`` rules that
    reference another step."""
    issues: list[Issue] = []
    fail = False
    complete = True
    names = {f.name for f in fields_}
    for k in entries:
        if k not in names:
            issues.append(Issue(k, IssueSeverity.ERROR, "UNKNOWN_FIELD", f"{k}: not a field of this step"))
    for f in fields_:
        v = entries.get(f.name)
        empty = v is None or (isinstance(v, str) and not v.strip()) or (f.kind == "confirm" and v is not True)
        required = f.required
        if f.required_when is not None:
            required = any(entry_condition(rc, entries, others) for rc in _as_list(f.required_when))
        if not empty:
            bad = next((r for r in f.invalid_when if (not r.get("values") or v in r["values"])
                        and any(entry_condition(rc, entries, others) for rc in _as_list(r.get("when")))), None)
            if bad is not None:
                issues.append(Issue(f.name, IssueSeverity.ERROR, "INVALID",
                                    f"{f.label}: {bad.get('text') or 'not allowed with the entered readings'}"))
                complete = False
                continue
        if empty:
            if required:
                issues.append(Issue(f.name, IssueSeverity.ERROR, "REQUIRED", f"{f.label}: entry required"))
                complete = False
            continue
        if f.kind == "number":
            x = _num(v)
            if x is None or isinstance(v, str):
                issues.append(Issue(f.name, IssueSeverity.ERROR, "NOT_A_NUMBER", f"{f.label}: {v!r} is not a number"))
                complete = False
                continue
            if (f.min is not None and x < f.min) or (f.max is not None and x > f.max):
                lo = "−∞" if f.min is None else f"{f.min:g}"
                hi = "∞" if f.max is None else f"{f.max:g}"
                issues.append(Issue(f.name, IssueSeverity.WARN, "OUT_OF_LIMITS",
                                    f"{f.label}: {x:g} {f.unit} outside {lo} … {hi} {f.unit}".replace("  ", " ")))
                fail = True
        elif f.kind == "choice":
            if v not in f.choices:
                issues.append(Issue(f.name, IssueSeverity.ERROR, "NOT_A_CHOICE", f"{f.label}: {v!r} not offered"))
                complete = False
                continue
        elif f.kind == "text" and not isinstance(v, str):
            issues.append(Issue(f.name, IssueSeverity.ERROR, "NOT_TEXT", f"{f.label}: text expected"))
            complete = False
            continue
        elif f.kind == "confirm" and v is not True:
            continue
        if f.pass_values and v not in f.pass_values:
            issues.append(Issue(f.name, IssueSeverity.WARN, "NOT_EXPECTED", f"{f.label}: {v!r} is not the expected "
                                f"result ({', '.join(map(str, f.pass_values))})"))
            fail = True
    if any(i.severity == IssueSeverity.ERROR for i in issues):
        return issues, None
    if fail:
        return issues, "FAIL"
    return issues, "PASS" if complete else None


# ============================================================================================ step record

def new_step_record() -> dict[str, Any]:
    return {"state": "NOT_DONE", "manual": False, "entries": {}, "entries_result": None, "remark": "",
            "skip_reason": "", "auto": {"state": "NA", "text": "", "stage": 0, "observed": [], "t_utc": None},
            "action": {"state": None, "text": "", "result": {}, "attempts": [], "verdict_fails": 0},
            "recordings": [], "history": [], "updated_utc": None, "origin": "", "mark_origin": ""}


def resolve_state(d: StepDef, rec: Mapping[str, Any]) -> str:
    """Step state from its parts (automatic result, engine / wizard action, operator entries) unless set manually."""
    if rec.get("manual"):
        return str(rec["state"])
    parts: list[str | None] = []
    if d.auto is not None:
        a = rec["auto"]["state"]
        parts.append(a if a in ("PASS", "FAIL") else None)
    if d.engine_action or d.wizard is not None:
        a = rec["action"]["state"]
        parts.append(a if a in ("PASS", "FAIL") else None)
    if d.fields:
        parts.append(rec.get("entries_result"))
    if not parts:
        return "NOT_DONE"
    if "FAIL" in parts:
        return "FAIL"
    if all(p == "PASS" for p in parts):
        return "PASS"
    return "NOT_DONE"


def step_origin(d: StepDef, rec: Mapping[str, Any]) -> str:
    """Who set the current state (pure): the ``mark`` origin for a manual state, else ``auto`` (automatic check,
    engine / wizard result) and / or ``operator`` (entries); "" while NOT_DONE."""
    if rec.get("manual"):
        return str(rec.get("mark_origin") or "operator")
    if rec.get("state", "NOT_DONE") == "NOT_DONE":
        return ""
    src: list[str] = []
    if d.auto is not None and rec["auto"]["state"] in ("PASS", "FAIL"):
        src.append("auto")
    if (d.engine_action or d.wizard is not None) and rec["action"]["state"] in ("PASS", "FAIL"):
        src.append("auto")
    if d.fields and rec.get("entries_result"):
        src.append("operator")
    return "+".join(dict.fromkeys(src))


# ============================================================================================ package (pure)

def check_session_doc(s: Any, where: str) -> dict[str, Any]:
    """Member types of a ``session.json`` (SWD-P3-08): a damaged file is ``FileFormatError``, never AttributeError."""
    if not isinstance(s, dict):
        raise FileFormatError(f"{where}: not an object")
    for key, typ in (("steps", dict), ("param_dumps", list), ("recordings", list), ("boards_seen", list),
                     ("log", list)):
        if key in s and not isinstance(s[key], typ):
            raise FileFormatError(f"{where}: '{key}' is not a{'n object' if typ is dict else ' list'}")
    if not isinstance(s.get("session_id", ""), str) or not isinstance(s.get("created_utc", ""), str):
        raise FileFormatError(f"{where}: session_id / created_utc not text")
    for k in ("board", "board_mismatch"):
        if s.get(k) is not None and not isinstance(s[k], dict):
            raise FileFormatError(f"{where}: '{k}' is not an object")
    for sid, rec in s.get("steps", {}).items():
        if not isinstance(rec, dict):
            raise FileFormatError(f"{where}: step record {sid!r} is not an object")
        for key, typ in (("auto", dict), ("action", dict), ("entries", dict), ("history", list),
                         ("recordings", list)):
            if key in rec and not isinstance(rec[key], typ):
                raise FileFormatError(f"{where}: step {sid!r} member '{key}' has the wrong type")
        if "state" in rec and rec["state"] not in STATES:
            raise FileFormatError(f"{where}: step {sid!r} state {rec['state']!r}")
    for x in s.get("recordings", ()) + s.get("param_dumps", ()) + s.get("boards_seen", ()):
        if not isinstance(x, dict):
            raise FileFormatError(f"{where}: list member is not an object")
    return s


incident_log = logging.getLogger("bend_stand.incident")


def _within(p: Path, roots: Iterable[Path]) -> bool:
    try:
        rp = p.resolve()
    except OSError:
        return False
    for r in roots:
        try:
            rp.relative_to(Path(r).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


def gui_settings_path(data_dir: Path) -> Path:
    """``gui.ini`` of this installation (env ``BEND_STAND_GUI_SETTINGS`` as in ``gui.settings``, else
    ``<data>/gui.ini``) — computed without importing Qt."""
    env = os.environ.get("BEND_STAND_GUI_SETTINGS", "").strip()
    return Path(env) if env else Path(data_dir) / "gui.ini"


def app_recording_roots(data_dir: Path) -> list[Path]:
    """Recordings roots the application itself knows (never taken from a ``session.json``, SWD-P3-07): the default
    root (env ``BEND_STAND_RECORDINGS_DIR`` or Documents) and the root stored in ``<data>/sessions/*.bbsession.json``."""
    roots = [paths.default_recordings_root()]
    ses = paths.sessions_dir(Path(data_dir))
    if ses.is_dir():
        for f in sorted(ses.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                r = d.get("recordings_root") if isinstance(d, dict) else None
            except (OSError, ValueError):
                continue
            if isinstance(r, str) and r.strip():
                roots.append(Path(r))
    return roots


def collect_files(session_dir: Path, data_dir: Path, session: Mapping[str, Any],
                  recording_roots: Iterable[str | os.PathLike[str]] | None = None) -> tuple[list[tuple[str, Path]],
                                                                                          list[str]]:
    """(archive name, source) pairs of SW-BRU-002 plus the notes of skipped items. Every path — also every
    recording folder named in ``session.json`` — is resolved (links / junctions / ``..``) and must lie below the
    data folder or a recordings root the application knows (``recording_roots``, else ``app_recording_roots``);
    anything else is skipped with a note and an incident line (SW-BRU-003, SWD-P3-07)."""
    data_dir = Path(data_dir)
    out: list[tuple[str, Path]] = []
    notes: list[str] = []
    roots = [Path(r) for r in (recording_roots if recording_roots is not None else app_recording_roots(data_dir))]
    allowed = [data_dir, *roots]
    gui_ini = gui_settings_path(data_dir)
    if gui_ini.parent.resolve() != data_dir.resolve():
        allowed.append(gui_ini.parent)

    def skip(what: Path) -> None:
        notes.append(f"skipped (outside the data folders): {what}")
        incident_log.warning("bring-up package: skipped %s (outside the application's data folders)", what)

    def add(arc: str, p: Path) -> None:
        if not p.is_file():
            return
        if not _within(p, allowed):
            skip(p)
            return
        out.append((arc, p))

    for p in sorted(Path(session_dir).rglob("*")):
        if p.is_file() and not p.name.endswith(".tmp"):
            add("session/" + p.relative_to(session_dir).as_posix(), p)
    logs = data_dir / "logs"
    if logs.is_dir():
        for p in sorted(logs.glob("*.log*")):
            add("logs/" + p.name, p)
    cal = paths.calibration_dir(data_dir)
    if cal.is_dir():
        for p in sorted(cal.rglob("*")):
            if p.is_file():
                add("calibration/" + p.relative_to(cal).as_posix(), p)
    ses = paths.sessions_dir(data_dir)
    if ses.is_dir():
        for p in sorted(ses.glob("*.json")):
            add("settings/sessions/" + p.name, p)
    add("settings/gui.ini", gui_ini)
    for r in session.get("recordings", ()):
        folder = Path(str(r.get("folder", "")))
        if not _within(folder, allowed):                    # checked first: a planted path is reported, not probed
            skip(folder)
            continue
        if not folder.is_dir():
            notes.append(f"recording folder missing: {folder}")
            continue
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                add(f"recordings/{folder.name}/" + p.relative_to(folder).as_posix(), p)
    seen: set[str] = set()
    uniq = []
    for arc, p in out:
        if arc not in seen:
            seen.add(arc)
            uniq.append((arc, p))
    return uniq, notes


def build_record(session: Mapping[str, Any], defs: Definitions, *, files: Sequence[str], notes: Sequence[str],
                 created_utc: str, vers: Mapping[str, Any]) -> dict[str, Any]:
    """``bringup.json`` (``bird.bend.bringup_record`` v1)."""
    recs = session.get("steps", {})
    steps = []
    counts = collections.Counter()
    for d in defs.steps:
        r = recs.get(d.id) or new_step_record()
        st = str(r.get("state", "NOT_DONE"))
        counts[st] += 1
        steps.append({"id": d.id, "section": d.section, "title": d.title, "kind": d.kind, "expected": d.expected,
                      "optional": d.optional, "stop_if_fail": d.stop_if_fail, "state": st,
                      "manual": bool(r.get("manual")), "origin": str(r.get("origin", "") or ""),
                      "entries": jsonable(r.get("entries", {})),
                      "remark": str(r.get("remark", "")), "skip_reason": str(r.get("skip_reason", "")),
                      "auto": jsonable(r.get("auto", {})), "action": jsonable(r.get("action", {})),
                      "recordings": [str(x) for x in r.get("recordings", ())],
                      "history": jsonable(r.get("history", [])), "updated_utc": r.get("updated_utc")})
    orphans = sorted(set(recs) - set(defs.ids))
    partial = session.get("closed_utc") is None or counts["NOT_DONE"] > 0
    cli = [s["id"] for s in steps if s["origin"] == "cli-test"]
    notes = list(notes)
    mm = session.get("board_mismatch")
    if mm:
        notes.insert(0, f"another board was connected to this session: {mm.get('connected_uid')} (session board "
                        f"{mm.get('session_uid')}) — no results were taken from it")
    if cli:
        notes.insert(0, f"{len(cli)} step(s) marked from the command line (origin cli-test, simulator smoke test — "
                        f"not operator evidence): {', '.join(cli)}")
    return {"schema": f"bird.bend.{RECORD_KIND}", "schema_version": RECORD_VERSION, "created_utc": created_utc,
            "partial": bool(partial),
            "session": {k: jsonable(session.get(k)) for k in ("session_id", "created_utc", "updated_utc",
                                                              "closed_utc", "board", "boards_seen", "current",
                                                              "board_mismatch",
                                                              "definitions", "param_dumps", "recordings",
                                                              "sw_version")},
            "summary": {"total": len(defs.steps), "pass": counts["PASS"], "fail": counts["FAIL"],
                        "skipped": counts["SKIPPED"], "not_done": counts["NOT_DONE"], "cli_test": len(cli)},
            "guide": jsonable(defs.guide), "versions": jsonable(vers), "steps": steps,
            "orphan_steps": {k: jsonable(recs[k]) for k in orphans}, "files": list(files), "notes": list(notes)}


#: script-free Content-Security-Policy, as the recording reports (SWR-11, SWD-P3-05)
_CSP = "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
_STATE_COLOR = {"PASS": "#1a7f1a", "FAIL": "#b00000", "SKIPPED": "#806000", "NOT_DONE": "#606060"}


def _origin_style(origin: str | None) -> str:
    return " style='color:#b00000;font-weight:bold'" if origin == "cli-test" else ""


def render_html(record: Mapping[str, Any], defs: Definitions) -> str:
    """Self-contained HTML report of a bring-up record (no scripts, no external resources)."""
    e = html.escape
    s = record["summary"]
    v = record["versions"]
    ses = record["session"]
    board = ses.get("board") or {}
    out = ["<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>", _CSP,
           f"<title>Bring-up {e(str(ses.get('session_id')))}</title>",
           "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;}table{border-collapse:collapse;"
           "width:100%;margin-bottom:18px}td,th{border:1px solid #bbb;padding:4px 6px;vertical-align:top;"
           "font-size:13px}th{background:#eee;text-align:left}.st{font-weight:bold}.small{color:#555;"
           "font-size:12px}</style></head><body>",
           f"<h1>Bird Bend Stand — bring-up record{' (partial)' if record['partial'] else ''}</h1>",
           ("<p style='color:#b00000;font-weight:bold'>" + e(f"{s.get('cli_test')} step(s) were marked from the "
            "command line (origin cli-test, simulator smoke test) — not operator evidence.") + "</p>")
           if s.get("cli_test") else "",
           "<table><tr><th>Session</th><td>" + e(str(ses.get("session_id"))) + "</td><th>Created</th><td>"
           + e(str(record["created_utc"])) + "</td></tr>",
           "<tr><th>Board UID</th><td>" + e(str(board.get("uid") or v.get("board_uid") or "–")) + "</td><th>FW</th>"
           "<td>" + e(f"{board.get('fw_version') or v.get('fw_version') or '–'} build "
                      f"{board.get('build') or v.get('fw_build') or '–'}") + "</td></tr>",
           "<tr><th>Application</th><td>" + e(f"{v.get('sw_version')} (ICD {v.get('icd_version')}, proto "
                                              f"{v.get('proto')}, dict {v.get('param_dict_hash')})") + "</td>"
           "<th>PC</th><td>" + e(f"{v.get('os')} · Python {v.get('python')}") + "</td></tr>",
           "<tr><th>Boards seen</th><td colspan='3'>" + e(", ".join(
               f"{b.get('uid')} (FW {b.get('fw_version')} build {b.get('build')})" for b in ses.get("boards_seen") or ())
               or "–") + "</td></tr>",
           ("<p style='color:#b00000;font-weight:bold'>" + e(
               f"Board changed: board {(ses.get('board_mismatch') or {}).get('connected_uid')} was connected to this "
               f"session of board {(ses.get('board_mismatch') or {}).get('session_uid')} — the session stopped "
               "taking results.") + "</p>") if ses.get("board_mismatch") else "",
           "<tr><th>Guide</th><td>" + e(f"{defs.guide.get('title', '')} {defs.guide.get('version', '')}") + "</td>"
           "<th>Result</th><td>" + e(f"{s['pass']} pass · {s['fail']} FAIL · {s['skipped']} skipped · "
                                     f"{s['not_done']} not done of {s['total']}") + "</td></tr></table>"]
    by_sec: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for st in record["steps"]:
        by_sec[st["section"]].append(st)
    for sec_id, sec_title in defs.sections:
        rows = by_sec.get(sec_id, [])
        if not rows:
            continue
        out.append(f"<h2>{e(sec_title)}</h2><table><tr><th>Step</th><th>Title / expected</th><th>State</th>"
                   "<th>Automatic result / action</th><th>Entries</th><th>Remark</th></tr>")
        for st in rows:
            auto = st.get("auto") or {}
            act = st.get("action") or {}
            res = " ".join(x for x in (f"[{auto.get('state')}] {auto.get('text', '')}" if auto.get("state") not in
                                       (None, "NA") else "",
                                       f"[action {act.get('state')}] {act.get('text', '')}" if act.get("state")
                                       else "") if x)
            ent = "; ".join(f"{k} = {val}" for k, val in (st.get("entries") or {}).items())
            rem = st.get("remark") or ""
            if st.get("skip_reason"):
                rem = f"skipped: {st['skip_reason']}" + (f" — {rem}" if rem else "")
            color = _STATE_COLOR.get(st["state"], "#000")
            out.append(f"<tr><td>{e(st['id'])}</td><td>{e(st['title'])}<div class='small'>{e(st['expected'])}"
                       f"</div></td><td class='st' style='color:{color}'>{e(st['state'])}"
                       f"<div class='small'{_origin_style(st.get('origin'))}>{e(st.get('origin') or '')}</div>"
                       f"</td><td>{e(res)}</td><td>{e(ent)}</td>"
                       f"<td>{e(rem)}</td></tr>")
        out.append("</table>")
    out.append("<h2>Collected files</h2><ul>" + "".join(f"<li>{e(f)}</li>" for f in record["files"]) + "</ul>")
    if record["notes"]:
        out.append("<h2>Notes</h2><ul>" + "".join(f"<li>{e(n)}</li>" for n in record["notes"]) + "</ul>")
    out.append("<p class='small'>manifest.json lists the SHA-256 of every file of this package.</p></body></html>")
    return "\n".join(out)


def package_name(created: _dt.datetime, uid: str | None, build: str | None) -> str:
    return f"bringup_{created.strftime('%Y%m%d')}_{_safe_name(uid, 'NOUID')}_{_safe_name(build, 'NOBUILD')}.zip"


def _sha_copy(src: Path, zf: zipfile.ZipFile, arc: str) -> tuple[int, str]:
    h = hashlib.sha256()
    n = 0
    with open(src, "rb") as fi, zf.open(arc, "w", force_zip64=True) as fo:
        while True:
            chunk = fi.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            fo.write(chunk)
            n += len(chunk)
    return n, h.hexdigest()


def build_package(session_dir: str | os.PathLike[str], out_dir: str | os.PathLike[str], *,
                  data_dir: str | os.PathLike[str], defs: Definitions | None = None,
                  info: Any = None, now: _dt.datetime | None = None,
                  recording_roots: Iterable[str | os.PathLike[str]] | None = None) -> BuPackage:
    """Write the bring-up zip (SW-BRU-003). Works offline from the session folder alone."""
    session_dir, out_dir, data_dir = Path(session_dir), Path(out_dir), Path(data_dir)
    session = check_session_doc(read_json(session_dir / "session.json", SESSION_KIND, SESSION_VERSION),
                                str(session_dir / "session.json"))
    if defs is None:
        used = session_dir / "steps_used.json"
        defs = load_definitions(used if used.is_file() else None)
    now = now or _dt.datetime.now(_dt.UTC)
    created = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    files, notes = collect_files(session_dir, data_dir, session, recording_roots)
    vers = versions(info)
    board = session.get("board") or {}
    if info is None and board:
        vers.update(fw_version=board.get("fw_version"), fw_build=board.get("build"), board_uid=board.get("uid"))
    arcs = [a for a, _p in files] + ["bringup.json", "report.html", RECORD_SCHEMA_FILE]
    record = build_record(session, defs, files=arcs, notes=notes, created_utc=created, vers=vers)
    schema = json.loads(resource_path(RECORD_SCHEMA_FILE).read_text(encoding="utf-8"))
    errs = minischema.validate(record, schema)
    if errs:
        raise FileFormatError("bring-up record does not validate: " + "; ".join(errs[:5]))
    out_dir.mkdir(parents=True, exist_ok=True)
    name = package_name(now, board.get("uid"), board.get("build"))
    target = out_dir / name
    k = 2
    while target.exists():
        target = out_dir / name.replace(".zip", f"_{k}.zip")
        k += 1
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(out_dir))
    os.close(fd)
    manifest: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for arc, p in files:
                try:
                    n, h = _sha_copy(p, zf, arc)
                except OSError as exc:
                    notes.append(f"not readable: {arc} ({exc})")
                    continue
                manifest.append({"path": arc, "size": n, "sha256": h})
            gen = {"bringup.json": json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                   "report.html": render_html(record, defs),
                   RECORD_SCHEMA_FILE: resource_path(RECORD_SCHEMA_FILE).read_text(encoding="utf-8")}
            for arc, text in gen.items():
                data = text.encode("utf-8")
                zf.writestr(arc, data)
                manifest.append({"path": arc, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            man = {"schema": f"bird.bend.{MANIFEST_KIND}", "schema_version": MANIFEST_VERSION, "created_utc": created,
                   "session_id": session.get("session_id"), "partial": record["partial"], "algorithm": "sha256",
                   "files": manifest}
            zf.writestr("manifest.json", json.dumps(man, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    h = hashlib.sha256(target.read_bytes()).hexdigest()
    return BuPackage(str(target), target.stat().st_size, len(manifest) + 1, defs.send_to, bool(record["partial"]), h)


def verify_package(zip_path: str | os.PathLike[str]) -> list[str]:
    """Problems of a package: manifest hashes / sizes, files not in the manifest, bringup.json schema."""
    problems: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        try:
            man = json.loads(zf.read("manifest.json"))
        except (KeyError, ValueError) as exc:
            return [f"manifest.json: {exc}"]
        listed = set()
        for f in man.get("files", ()):
            listed.add(f["path"])
            try:
                data = zf.read(f["path"])
            except KeyError:
                problems.append(f"missing: {f['path']}")
                continue
            if len(data) != f["size"] or hashlib.sha256(data).hexdigest() != f["sha256"]:
                problems.append(f"hash mismatch: {f['path']}")
        for n in sorted(names - listed - {"manifest.json"}):
            problems.append(f"not in the manifest: {n}")
        try:
            rec = json.loads(zf.read("bringup.json"))
            schema = json.loads(zf.read(RECORD_SCHEMA_FILE))
            problems += [f"bringup.json {x}" for x in minischema.validate(rec, schema)]
        except (KeyError, ValueError) as exc:
            problems.append(f"bringup.json: {exc}")
    return problems


def list_sessions(data_dir: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Sessions in ``<data>/bringup/sessions`` (newest first): id, folder, created, closed."""
    root = sessions_root(Path(data_dir))
    out = []
    if root.is_dir():
        for d in root.iterdir():
            f = d / "session.json"
            if not f.is_file():
                continue
            try:
                s = read_json(f, SESSION_KIND, SESSION_VERSION)
            except FileFormatError:
                continue
            out.append({"session_id": s.get("session_id", d.name), "folder": str(d),
                        "created_utc": s.get("created_utc"), "closed_utc": s.get("closed_utc")})
    out.sort(key=lambda x: str(x["created_utc"]), reverse=True)
    return out


def find_session(data_dir: str | os.PathLike[str], ref: str | None) -> Path:
    """Session folder from an id, a folder path, or None (the active one, else the newest)."""
    if ref:
        p = Path(ref)
        if (p / "session.json").is_file():
            return p
        q = sessions_root(Path(data_dir)) / ref
        if (q / "session.json").is_file():
            return q
        raise FileNotFoundError(f"no bring-up session {ref!r}")
    act = Path(data_dir) / "bringup" / "active.json"
    try:
        sid = json.loads(act.read_text(encoding="utf-8")).get("session_id")
        q = sessions_root(Path(data_dir)) / str(sid)
        if sid and (q / "session.json").is_file():
            return q
    except (OSError, ValueError, AttributeError):
        pass
    ss = list_sessions(data_dir)
    if not ss:
        raise FileNotFoundError("no bring-up session found")
    return Path(ss[0]["folder"])


# ============================================================================================ pulse integrity

@dataclass
class _Pulse:
    """One pulse-integrity run (D-56 b, SYS-011, E's review): baseline re-homes d0ᵢ, round trips through the normal
    motion API, final re-home d1; d from EVENT HOMED value only."""

    step_id: str
    p: Mapping[str, Any]
    confirmed: bool
    v_mm_s: float
    ops: list[tuple[str, Any]]
    started_utc: str
    idx: int = 0
    d0: list[int] = field(default_factory=list)
    d1: int | None = None
    home_issued: bool = False
    home_ticket_done: bool = False
    home_ticket_ns: int | None = None
    homed_value: int | None = None
    drift_fault: bool = False
    raw: list[str] = field(default_factory=list)
    moves_done: int = 0
    ended: bool = False


# ============================================================================================ engine

class BringupEngine:
    """``backend.bringup`` (api.BringupAPI). Thread-safe; GUI calls never block on the board."""

    def __init__(self, be: Any, *, steps_path: str | None = None, out_dir: str | None = None) -> None:
        self._be = be
        self._lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._steps_path = steps_path
        self._out_dir = out_dir
        self.defs: Definitions | None = None
        self.defs_error: str | None = None
        self._session: dict[str, Any] | None = None
        self._dir: Path | None = None
        self._q: collections.deque[tuple[str, Any]] = collections.deque()
        self._dropped = 0
        self._dirty = False
        self._pending_pub: list[tuple[str, Any]] = []
        self._token: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._next_eval = 0
        self._next_drain = 0
        self._next_snapshot = 0
        self._armed_ns: int | None = None
        self._stage_ns: int | None = None
        self._ev_buf: list[Any] = []
        self._topics_seen: set[str] = set()
        self._last_bits: tuple[int, int] | None = None
        self._last_board_key: tuple | None = None
        self._last_nvm_seq: int | None = None
        self._last_seq_state: str | None = None
        self._rec_own: str | None = None
        self._rec_step: str | None = None
        self._rec_req: str | None = None
        self._pulse: _Pulse | None = None
        self._dump_req: list[str] = []
        self._events_fh: Any = None
        self._gen = 0
        self._written_gen = 0
        self._last_save_cache: tuple[str, Mapping[str, Any] | None] | None = None
        self._rec_skip_step: str | None = None
        self._own_roots: set[Path] = set()      # roots this process recorded into (trusted, SWD-P3-07)
        self._epoch_ns = 0                      # last connect / BOOT (B-BRU-04 item 7: older board data is stale)
        self._hold_ns: int | None = None        # stage conditions hold since (min_s)
        self._pulse_last: tuple[_Pulse, int, str] | None = None   # (run, end time, abort cause) for the BOOT rule
        self.last_package: str | None = None
        self._load_defs()

    # ------------------------------------------------------------------------------------------ lifecycle
    def _load_defs(self) -> None:
        try:
            self.defs = load_definitions(self._steps_path)
            self.defs_error = None
        except FileFormatError as exc:
            self.defs, self.defs_error = None, str(exc)
            log.error("bring-up step definitions not usable: %s", exc)

    @property
    def data_dir(self) -> Path:
        return Path(self._be.data_dir)

    def out_dir(self) -> Path:
        return Path(self._out_dir) if self._out_dir else default_bringup_root()

    def attach(self) -> None:
        """Backend start: subscribe, resume the active session (SW-BRU-002)."""
        if self._token is None:
            self._token = self._be.events.subscribe("*", self._on_record, weak=False)
        self._resume_active()
        if not self._be.lockstep and self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="bend-bringup", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        with self._lock:
            pr = self._pulse
            if pr is not None and not pr.ended:
                self._pulse_end(pr, "ABORTED", "interrupted: application closed — run the check again")
        self._stop.set()
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(2.0)
        try:
            self.flush()
        except Exception:  # noqa: BLE001 — shutdown never raises
            log.exception("bring-up flush at shutdown failed")
        with self._io_lock:
            fh, self._events_fh = self._events_fh, None
            if fh is not None:
                fh.close()
        if self._token is not None:
            self._be.events.unsubscribe(self._token)
            self._token = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(0.2)
            self._wake.clear()
            try:
                self._drain()
            except Exception:  # noqa: BLE001 — the writer survives a bad batch
                log.exception("bring-up writer failed")

    # ------------------------------------------------------------------------------------------ queries
    def definitions(self) -> Definitions | None:
        return self.defs

    def sections(self) -> list[BuSection]:
        d = self.defs
        if d is None:
            return []
        return [BuSection(sid, title, tuple(s.id for s in d.steps if s.section == sid)) for sid, title in d.sections]

    def steps(self) -> list[BuStep]:
        d = self.defs
        if d is None:
            return []
        with self._lock:
            return [self._bustep(s) for s in d.steps]

    def step(self, step_id: str) -> BuStep:
        if self.defs is None:
            raise KeyError(step_id)
        with self._lock:
            return self._bustep(self.defs.step(step_id))

    def _rec(self, step_id: str) -> dict[str, Any]:
        assert self._session is not None
        return self._session["steps"].setdefault(step_id, new_step_record())

    def _bustep(self, d: StepDef) -> BuStep:
        rec = (self._session or {}).get("steps", {}).get(d.id) or new_step_record()
        a, act = rec["auto"], rec["action"]
        auto = None
        if d.auto is not None:
            auto = BuAuto(a["state"], a["text"])
        elif d.engine_action or d.wizard is not None:
            st = act["state"]
            auto = BuAuto({"RUNNING": "WAITING", "PASS": "PASS", "FAIL": "FAIL"}.get(st or "", "NA"), act["text"])
        return BuStep(d.id, d.section, d.title, d.instruction, d.expected, d.display_kind, d.fields, d.action,
                      d.motion, d.stop_if_fail, d.safety, d.hg, auto, rec["state"], dict(rec["entries"]),
                      rec["remark"], rec["skip_reason"], d.kind, d.optional, d.engine_action, act["state"],
                      act["text"], bool(rec["manual"]), str(rec.get("origin", "") or ""))

    def status(self) -> BuStatus:
        with self._lock:
            s = self._session
            d = self.defs
            defs_txt = "" if d is None else f"{d.guide.get('title', '')} {d.guide.get('version', '')}".strip()
            if s is None or d is None:
                return BuStatus(definitions=defs_txt if d is not None else f"step file not usable: {self.defs_error}",
                                last_package=self.last_package)
            states = [(s["steps"].get(x.id) or {}).get("state", "NOT_DONE") for x in d.steps]
            board = s.get("board") or {}
            pr = self._pulse
            return BuStatus(s.get("closed_utc") is None, s["session_id"], board.get("uid"), board.get("build"),
                            s["created_utc"], len(states), sum(x != "NOT_DONE" for x in states),
                            states.count("PASS"), states.count("FAIL"), states.count("SKIPPED"), s.get("current"),
                            str(self._dir), s.get("closed_utc"), None if pr is None or pr.ended else pr.step_id,
                            self.last_package, defs_txt, self._mismatch_text())

    def sessions(self) -> list[dict[str, Any]]:
        return list_sessions(self.data_dir)

    # ------------------------------------------------------------------------------------------ session
    def _now_ns(self) -> int:
        return self._be.clock.monotonic_ns()

    def _utc(self) -> str:
        return utc_iso(self._be.clock.wall_ns())

    def start(self, *, confirmed: bool = False) -> GateResult:
        """GUI ``start``: a new session (CONFIRM when one is open; the old one stays in its folder)."""
        return self.start_session(confirmed=confirmed)

    def start_session(self, *, confirmed: bool = False) -> GateResult:
        if self.defs is None:
            return GateResult((GateItem("STEP_FILE_INVALID", Severity.REFUSE,
                                        f"bring-up step file not usable: {self.defs_error}"),))
        with self._lock:
            s = self._session
            if self._pulse is not None and not self._pulse.ended:
                return GateResult((GateItem("OPERATION_RUNNING", Severity.REFUSE,
                                            "the pulse-integrity check is running"),))
            if s is not None and s.get("closed_utc") is None and not confirmed:
                done = sum(1 for r in s["steps"].values() if r.get("state") != "NOT_DONE")
                mm = s.get("board_mismatch")
                lead = (f"board {mm.get('connected_uid')} is connected, the session belongs to board "
                        f"{mm.get('session_uid')} — start a new session for this board? " if mm else
                        f"a bring-up session exists ({done} steps done) — start a new one? ")
                return GateResult((GateItem("SESSION_EXISTS", Severity.CONFIRM,
                                            lead + "The old session stays in its folder and can still be packed."),))
        if s is not None and s.get("closed_utc") is None:
            self.close_session("replaced by a new session")
        self._open_new()
        return GATE_OK

    def _open_new(self) -> None:
        be = self._be
        wall = be.clock.wall_ns()
        sid = "bu-" + _dt.datetime.fromtimestamp(wall / 1e9, tz=_dt.UTC).strftime("%Y%m%d-%H%M%S")
        root = sessions_root(self.data_dir)
        folder = root / sid
        k = 2
        while folder.exists():
            folder = root / f"{sid}-{k}"
            k += 1
        folder.mkdir(parents=True)
        from bend_stand import __version__  # noqa: PLC0415

        assert self.defs is not None
        info = be.device.info
        with self._lock:
            self._close_events_file()
            self._dir = folder
            self._session = {"schema": f"bird.bend.{SESSION_KIND}", "schema_version": SESSION_VERSION,
                             "session_id": folder.name, "created_utc": self._utc(), "updated_utc": None,
                             "closed_utc": None, "sw_version": __version__,
                             "definitions": {"source": Path(self.defs.source).name, "sha256": self.defs.sha256,
                                             "guide": jsonable(self.defs.guide)},
                             "board": None, "boards_seen": [], "current": self.defs.steps[0].id,
                             "steps": {d.id: new_step_record() for d in self.defs.steps}, "param_dumps": [],
                             "recordings": [], "log": []}
            if info is not None:
                self._set_board(info)
            self._reset_watch()
            self._session_log("SESSION_START", "")
        self._save()
        (folder / "steps_used.json").write_text(json.dumps(self.defs.raw, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
        try:
            atomic_write_json(folder / "settings_start.json", {"session_settings": jsonable(be.session.get())})
        except (OSError, ValueError) as exc:
            log.warning("bring-up: session settings not saved: %s", exc)
        self._write_info(info)
        self._dump_params("start")
        self._snapshot()
        atomic_write_json(self.data_dir / "bringup" / "active.json", {"session_id": folder.name})
        self._arm_current()
        self._publish_state()

    def close_session(self, why: str = "operator") -> GateResult:
        """End the session: final parameter dump, ``closed_utc``; it can still be packed."""
        with self._lock:
            s = self._session
            if s is None or s.get("closed_utc") is not None:
                return GateResult((GateItem("NO_SESSION", Severity.REFUSE, "no open bring-up session"),))
            if self._pulse is not None and not self._pulse.ended:
                return GateResult((GateItem("OPERATION_RUNNING", Severity.REFUSE,
                                            "the pulse-integrity check is running"),))
        self._dump_params("end")
        self._snapshot()
        self.flush()
        with self._lock:
            s["closed_utc"] = self._utc()
            self._session_log("SESSION_END", why)
            self._disarm()
        self.flush()
        self._close_events_file()
        try:
            (self.data_dir / "bringup" / "active.json").unlink()
        except OSError:
            pass
        self._publish_state()
        return GATE_OK

    def resume(self, session_id: str) -> GateResult:
        """Open an older session again (it becomes the active one)."""
        try:
            folder = find_session(self.data_dir, session_id)
        except FileNotFoundError as exc:
            return GateResult((GateItem("NO_SESSION", Severity.REFUSE, str(exc)),))
        with self._lock:
            if self._pulse is not None and not self._pulse.ended:
                return GateResult((GateItem("OPERATION_RUNNING", Severity.REFUSE,
                                            "the pulse-integrity check is running"),))
        cur = self._session
        if cur is not None and cur.get("closed_utc") is None and self._dir != folder:
            self.close_session("another session resumed")
        if not self._load_session(folder, reopen=True):
            return GateResult((GateItem("SESSION_UNREADABLE", Severity.REFUSE,
                                        f"bring-up session {folder.name} not readable ({folder}) — see the log"),))
        atomic_write_json(self.data_dir / "bringup" / "active.json", {"session_id": folder.name})
        self._publish_state()
        return GATE_OK

    def _resume_active(self) -> None:
        act = self.data_dir / "bringup" / "active.json"
        if not act.is_file() or self.defs is None:
            return
        try:
            sid = json.loads(act.read_text(encoding="utf-8")).get("session_id")
        except (OSError, ValueError, AttributeError):
            return
        folder = sessions_root(self.data_dir) / str(sid)
        if self._load_session(folder, reopen=False):
            self._publish_state()

    def _load_session(self, folder: Path, *, reopen: bool) -> bool:
        try:
            s = check_session_doc(read_json(folder / "session.json", SESSION_KIND, SESSION_VERSION),
                                  str(folder / "session.json"))
        except FileFormatError as exc:
            log.error("bring-up session not resumed: %s", exc)
            return False
        assert self.defs is not None
        for d in self.defs.steps:
            s.setdefault("steps", {}).setdefault(d.id, new_step_record())
        for k, v in (("param_dumps", []), ("recordings", []), ("boards_seen", []), ("log", [])):
            s.setdefault(k, v)
        for sid, rec in s["steps"].items():
            base = new_step_record()
            for k, v in base.items():
                rec.setdefault(k, v)
            if rec["action"].get("state") == "RUNNING":          # interrupted by the restart (kill-safe resume)
                rec["action"]["state"] = "ABORTED"
                rec["action"]["text"] = "interrupted by an application restart — run it again"
                rec["history"].append({"t_utc": self._utc(), "what": "ACTION_INTERRUPTED"})
            if rec["auto"].get("state") == "WAITING":
                rec["auto"]["state"] = "NA"
            d = next((x for x in self.defs.steps if x.id == sid), None)
            if d is not None:
                rec["state"] = resolve_state(d, rec)
        if reopen:
            s["closed_utc"] = None
        with self._lock:
            self._close_events_file()
            self._session, self._dir = s, folder
            self._reset_watch()
            self._session_log("SESSION_RESUMED", "reopened" if reopen else "application start")
        self._save()
        if s.get("current") in self.defs.ids:
            self._arm_current()
        return True

    def _reset_watch(self) -> None:
        self._last_bits = None
        self._last_board_key = None
        self._last_nvm_seq = None
        self._next_snapshot = 0
        self._disarm()

    def _session_log(self, what: str, text: str) -> None:
        if self._session is not None:
            self._session.setdefault("log", []).append({"t_utc": self._utc(), "what": what, "text": text})
            self._q.append(("bringup", {"what": what, "text": text}))

    def _set_board(self, info: Any) -> None:
        s = self._session
        if s is None:
            return
        b = {"uid": info.uid, "fw_version": ".".join(map(str, info.fw_version)), "build": info.build,
             "proto": f"{info.proto_major}.{info.proto_minor}", "param_dict_hash": f"0x{int(info.param_dict_hash):08X}",
             "features": sorted(info.features), "t_utc": self._utc()}
        if s.get("board") is None:
            s["board"] = b
        elif s["board"].get("uid") != info.uid:                  # SWD-P3-04: never mix boards silently
            if (s.get("board_mismatch") or {}).get("connected_uid") != info.uid:
                s["board_mismatch"] = {"session_uid": s["board"].get("uid"), "connected_uid": info.uid,
                                       "t_utc": self._utc()}
                self._session_log("BOARD_CHANGED", f"{s['board'].get('uid')} → {info.uid}: the session refuses to "
                                                   "continue (new session for this board, or package the old one)")
                self._disarm()
                self._pending_pub.append(("bringup.state", None))
        elif s.get("board_mismatch") is not None:
            s["board_mismatch"] = None
            self._session_log("BOARD_RESTORED", info.uid)
            self._pending_pub.append(("bringup.state", None))
        if not any(x.get("uid") == b["uid"] and x.get("build") == b["build"] for x in s["boards_seen"]):
            s["boards_seen"].append(b)

    # ------------------------------------------------------------------------------------------ persistence
    def _save(self) -> None:
        """Write ``session.json`` now (atomic): snapshot under the lock, file write outside it (kill-safe)."""
        with self._lock:
            s, d = self._session, self._dir
            if s is None or d is None:
                return
            s["updated_utc"] = self._utc()
            try:
                doc = json.loads(json.dumps(jsonable(s), allow_nan=False))
            except (TypeError, ValueError) as exc:
                log.error("bring-up session not serialisable: %s", exc)
                return
            self._gen += 1
            gen = self._gen
            self._dirty = False
        with self._io_lock:
            if gen <= self._written_gen:
                return                                     # a newer snapshot is already on disk
            try:
                atomic_write_json(d / "session.json", doc)
                self._written_gen = gen
            except OSError as exc:
                log.error("bring-up session not saved: %s", exc)
                self._dirty = True

    def _close_events_file(self) -> None:
        with self._io_lock:
            fh, self._events_fh = self._events_fh, None
            if fh is not None:
                fh.close()

    def flush(self) -> None:
        """Write everything queued (events, dumps, session) now."""
        self._drain(force=True)

    def _write_info(self, info: Any) -> None:
        if self._dir is None or info is None:
            return
        try:
            atomic_write_json(self._dir / "info.json", {"t_utc": self._utc(), "info": jsonable(info),
                                                        "features": sorted(info.features)})
        except (OSError, ValueError) as exc:
            log.warning("bring-up info.json not written: %s", exc)

    def _dump_params(self, tag: str) -> None:
        """Full board parameter set (cache of the last read / write / save; SW-BRU-002)."""
        with self._lock:
            s, d = self._session, self._dir
            if s is None or d is None:
                return
            n = len(s["param_dumps"]) + 1
        be = self._be
        try:
            values = dict(be.config.values())
        except Exception:  # noqa: BLE001
            values = {}
        st = be.status()
        board = st.board
        doc = {"t_utc": self._utc(), "tag": tag, "connected": st.link.state == LinkState.CONNECTED,
               "board_uid": None if st.link.info is None else st.link.info.uid,
               "nvm_record_seq": None if board is None else board.nvm_record_seq, "cfg_dirty": st.cfg_dirty,
               "nvm_defaulted": st.nvm_defaulted, "count": len(values), "values": jsonable(values)}
        name = f"params/params_{n:03d}_{_safe_name(tag, 'dump')}.json"
        try:
            atomic_write_json(d / name, doc)
        except (OSError, ValueError) as exc:
            log.error("bring-up parameter dump not written: %s", exc)
            return
        with self._lock:
            if self._session is s:
                s["param_dumps"].append({"file": name, "tag": tag, "t_utc": doc["t_utc"], "count": len(values),
                                         "nvm_record_seq": doc["nvm_record_seq"]})
                self._dirty = True

    def _last_save_values(self) -> Mapping[str, Any] | None:
        """Values of the newest NVM-save dump (cached by file name)."""
        s, d = self._session, self._dir
        if s is None or d is None:
            return None
        name = next((x["file"] for x in reversed(s["param_dumps"]) if x["tag"] == "nvm_save"), None)
        if name is None:
            return None
        c = self._last_save_cache
        if c is not None and c[0] == str(d / name):
            return c[1]
        try:
            vals = json.loads((d / name).read_text(encoding="utf-8"))["values"]
        except (OSError, ValueError, KeyError):
            vals = None
        self._last_save_cache = (str(d / name), vals)
        return vals

    def _snapshot(self) -> None:
        st = self._be.status()
        b = st.board
        self._q.append(("snapshot", {"kind": "link_stats", "state": st.link.state.value,
                                     "stats": jsonable(st.link.stats)}))
        self._q.append(("snapshot", {"kind": "nvm", "cfg_dirty": st.cfg_dirty, "nvm_defaulted": st.nvm_defaulted,
                                     "reboot_pending": st.reboot_pending,
                                     "nvm_record_seq": None if b is None else b.nvm_record_seq,
                                     "nvm_save_ms": None if b is None else b.nvm_save_ms}))

    # ------------------------------------------------------------------------------------------ event intake
    def _on_record(self, rec: Any) -> None:
        """EventBus ``*`` observer (any thread, < 1 ms): queue for the writer, feed the evaluation."""
        s = self._session
        if s is None or s.get("closed_utc") is not None or rec.topic in SKIP_TOPICS:
            return
        topic = rec.topic
        if topic == "seq.status":
            state = str(getattr(rec.payload, "state", ""))
            if state == self._last_seq_state:
                return
            self._last_seq_state = state
        if len(self._q) >= MAX_QUEUE:
            self._dropped += 1
        else:
            self._q.append(("event", rec))
        with self._lock:
            if self._session is None or self._session.get("closed_utc") is not None:
                return
            if topic == "fw.event":
                if getattr(rec.payload, "name", "") == "BOOT":
                    self._epoch_ns = max(self._epoch_ns, rec.t_host_ns)
                if self._armed_ns is not None:
                    self._ev_buf.append(rec.payload)
                self._pulse_on_fw_event(rec.payload, rec.t_host_ns)
            elif topic == "link.state":
                st = getattr(rec.payload, "state", None)
                if st == LinkState.CONNECTED:
                    self._epoch_ns = max(self._epoch_ns, rec.t_host_ns)
                elif st is not None:
                    self._pulse_disturb(f"link {getattr(st, 'value', st)}", kind="LINK")
            elif topic == "device.info":
                self._set_board(rec.payload)
                self._dirty = True
            if self._armed_ns is not None:
                self._topics_seen.add(topic)
            for wz, tp in WIZARD_TOPIC.items():
                if topic == tp:
                    self._on_wizard(wz, rec.payload)
        if topic == "device.info":
            self._write_info(rec.payload)

    # ------------------------------------------------------------------------------------------ writer
    def _drain(self, force: bool = False) -> None:
        with self._lock:
            d = self._dir
            items = []
            while self._q:
                items.append(self._q.popleft())
            dumps, self._dump_req = self._dump_req, []
            rec_req, self._rec_req = self._rec_req, None
        for tag in dumps:
            self._dump_params(tag)
        if rec_req is not None:
            self._record_action(rec_req)
        if d is not None and items:
            lines = []
            for kind, x in items:
                try:
                    lines.append(json.dumps(self._line(kind, x), ensure_ascii=False, allow_nan=False))
                except (TypeError, ValueError) as exc:
                    lines.append(json.dumps({"kind": "unserialisable", "error": str(exc)}))
            with self._io_lock:
                try:
                    if self._events_fh is None:
                        self._events_fh = open(d / "events.jsonl", "a", encoding="utf-8", newline="\n")  # noqa: SIM115
                    snaps = [ln for (k, _x), ln in zip(items, lines, strict=True) if k == "snapshot"]
                    evs = [ln for (k, _x), ln in zip(items, lines, strict=True) if k != "snapshot"]
                    if evs:
                        self._events_fh.write("\n".join(evs) + "\n")
                        self._events_fh.flush()
                        os.fsync(self._events_fh.fileno())
                    if snaps:
                        with open(d / "snapshots.jsonl", "a", encoding="utf-8", newline="\n") as f:
                            f.write("\n".join(snaps) + "\n")
                except OSError as exc:
                    log.error("bring-up events not written: %s", exc)
        if self._dirty or force:
            self._save()
        self._flush_publish()

    def _line(self, kind: str, x: Any) -> dict[str, Any]:
        if kind == "event":
            return {"t_utc": utc_iso(self._be.clock.wall_ns() - (self._now_ns() - x.t_host_ns)), "t_ns": x.t_host_ns,
                    "topic": x.topic, "payload": jsonable(x.payload)}
        return {"t_utc": self._utc(), "t_ns": self._now_ns(), "topic": f"bringup.{kind}", "payload": jsonable(x)}

    # ------------------------------------------------------------------------------------------ tick
    def tick(self, now: int) -> None:
        """Supervisor tick (any rate; real work every 100 ms): DATA / STATUS change capture, NVM-save detection,
        evaluation of the selected step, motion-step recording, pulse-integrity timeouts. No file I/O on the real
        clock (writer thread); on the lock-step clock the writer runs here (deterministic)."""
        s = self._session
        if s is None:
            return
        be = self._be
        flags, dstat = be.device.latest()
        if (flags, dstat) != self._last_bits:
            prev, self._last_bits = self._last_bits, (flags, dstat)
            if s.get("closed_utc") is None:
                self._q.append(("data_bits", {"flags": _bits(pg.DATA_FLAGS_BITS, flags),
                                              "status": _bits(pg.DATA_STATUS_BITS, dstat),
                                              "first": prev is None}))
                if self._rec_step is not None or self._wants_recording(flags):
                    self._wake.set()
        if s.get("closed_utc") is None:
            board = be.device.board
            if board is not None:
                key = (board.flags, board.status, board.faults, board.io, board.sys_flags, board.home_phase,
                       board.motion_state, board.nvm_record_seq, board.halt_src, board.pause_src, board.reset_cause)
                if key != self._last_board_key:
                    self._last_board_key = key
                    self._q.append(("status", {"faults": _bits(pg.FAULTS_BITS, board.faults),
                                               "io": _bits(pg.IO_BITS, board.io),
                                               "sys": _bits(pg.SYS_FLAGS_BITS, board.sys_flags),
                                               "flags": _bits(pg.DATA_FLAGS_BITS, board.flags),
                                               "status": _bits(pg.DATA_STATUS_BITS, board.status),
                                               "home_phase": board.home_phase, "motion_state": board.motion_state,
                                               "nvm_record_seq": board.nvm_record_seq, "halt_src": board.halt_src,
                                               "pause_src": board.pause_src, "reset_cause": board.reset_cause}))
                    seq = int(board.nvm_record_seq)
                    if self._last_nvm_seq is not None and seq != self._last_nvm_seq:
                        with self._lock:
                            self._dump_req.append("nvm_save")
                            self._session_log("NVM_SAVED", f"record {self._last_nvm_seq} → {seq}")
                            self._dirty = True
                    self._last_nvm_seq = seq
            if now >= self._next_eval:
                self._next_eval = now + EVAL_PERIOD_NS
                self._evaluate(now)
                self._recording_tick(flags)
                self._pulse_tick(now)
            if now >= self._next_snapshot:
                self._next_snapshot = now + SNAPSHOT_PERIOD_NS
                self._snapshot()
        if be.lockstep:
            if now >= self._next_drain:
                self._next_drain = now + DRAIN_PERIOD_NS
                self._drain()
        else:
            self._flush_publish_async()

    def _flush_publish_async(self) -> None:
        if self._pending_pub or self._dirty:
            self._wake.set()

    # ------------------------------------------------------------------------------------------ evaluation
    def _disarm(self) -> None:
        self._armed_ns = None
        self._stage_ns = None
        self._hold_ns = None
        self._ev_buf = []
        self._topics_seen = set()

    def _arm_current(self) -> None:
        with self._lock:
            s = self._session
            if s is None or self.defs is None or s.get("closed_utc") is not None:
                return
            cur = s.get("current")
            self._disarm()
            try:
                d = self.defs.step(cur)
            except KeyError:
                return
            rec = self._rec(d.id)
            if d.auto is None or d.auto.get("arm", "select") != "select" or rec["manual"]:
                return
            if rec["auto"]["state"] in ("PASS", "FAIL") or s.get("board_mismatch"):
                return
            self._arm_locked(d, rec)

    def _arm_locked(self, d: StepDef, rec: dict[str, Any]) -> None:
        now = self._now_ns()
        self._armed_ns = self._stage_ns = now
        self._ev_buf, self._topics_seen = [], set()
        assert d.auto is not None
        stages = d.auto["stages"]
        rec["auto"].update(state="WAITING", stage=0, observed=[], t_utc=None,
                           text=stages[0].get("text") or "waiting …")
        self._changed(d, rec, "AUTO_ARMED", "")

    def _context(self) -> EvalContext:
        be = self._be
        st = be.status()
        flags, dstat = be.device.latest()
        try:
            params = dict(be.config.values())
        except Exception:  # noqa: BLE001
            params = {}
        nvm: dict[str, Any] = {"record_seq": None if st.board is None else st.board.nvm_record_seq}
        s = self._session or {}
        dumps = s.get("param_dumps", [])
        nvm["saves"] = sum(1 for x in dumps if x.get("tag") == "nvm_save")
        nvm["dumps"] = len(dumps)
        last = self._last_save_values()
        nvm["matches_last_save"] = None if last is None or not params else all(
            _eq(params.get(k), v) for k, v in last.items())
        return EvalContext(st, flags, dstat, params, list(self._ev_buf), set(self._topics_seen), nvm, _constants(),
                           dict(s.get("steps", {})))

    def _fresh_ns(self) -> int:
        """Receive time of the newest board data (DATA flags or GET_STATUS)."""
        dev = self._be.device
        return max(int(getattr(dev, "last_flags_ns", 0) or 0), int(getattr(dev, "board_ns", 0) or 0))

    def _board_fresh(self, since_ns: int) -> bool:
        """B-BRU-04 item 7: link CONNECTED and board data newer than ``since_ns`` and the last connect / BOOT —
        stale or default flags never satisfy a condition."""
        if self._be.device.state != LinkState.CONNECTED:
            return False
        return self._fresh_ns() > max(int(since_ns or 0), self._epoch_ns)

    def _evaluate(self, now: int) -> None:
        if self._armed_ns is None or self.defs is None or (self._session or {}).get("board_mismatch"):
            return
        ctx = self._context()
        with self._lock:
            s = self._session
            if s is None or self._armed_ns is None:
                return
            try:
                d = self.defs.step(s.get("current"))
            except KeyError:
                return
            rec = self._rec(d.id)
            a = rec["auto"]
            if d.auto is None or a["state"] != "WAITING":
                return
            ctx.events = list(self._ev_buf)
            ctx.topics = set(self._topics_seen)
            tmo = d.auto.get("timeout_s")
            stages = d.auto["stages"]
            if not self._board_fresh(self._stage_ns or 0):          # global freshness rule (B-BRU-04 item 7)
                self._hold_ns = None
                if tmo is not None and self._armed_ns is not None and now - self._armed_ns > float(tmo) * 1e9:
                    a.update(state="FAIL", text=f"FAIL — timeout after {float(tmo):g} s; no fresh board data "
                                                "(link / stream)", t_utc=self._utc())
                    self._disarm()
                    self._changed(d, rec, "AUTO_TIMEOUT", a["text"])
                return
            for c in d.auto.get("fail_if", ()):
                ok, obs = eval_condition(c, ctx)
                if ok:
                    a.update(state="FAIL", text="FAIL — " + cond_text(c, obs), t_utc=self._utc())
                    a["observed"].append(cond_text(c, obs))
                    self._disarm()
                    self._changed(d, rec, "AUTO_FAIL", a["text"])
                    return
            advanced = False
            stg = stages[a["stage"]]
            res = [eval_condition(c, ctx) for c in stg["all"]]
            if all(ok for ok, _o in res):
                min_s = float(stg.get("min_s") or 0.0)
                if self._hold_ns is None:
                    self._hold_ns = now
                if now - self._hold_ns >= min_s * 1e9:              # held continuously for min_s (E's schema item)
                    a["observed"] += [cond_text(c, o) for c, (_ok, o) in zip(stg["all"], res, strict=True)]
                    if min_s:
                        a["observed"].append(f"held ≥ {min_s:g} s")
                    a["stage"] += 1
                    advanced = True
                    self._ev_buf, self._topics_seen = [], set()      # the next stage needs newer data / events
                    self._stage_ns = now
                    self._hold_ns = None
            else:
                self._hold_ns = None
            if a["stage"] >= len(stages):
                a.update(state="PASS", text="PASS — " + "; ".join(a["observed"]), t_utc=self._utc())
                self._disarm()
                self._changed(d, rec, "AUTO_PASS", a["text"])
                return
            if tmo is not None and self._armed_ns is not None and now - self._armed_ns > float(tmo) * 1e9:
                pending = [cond_text(c, eval_condition(c, ctx)[1]) for c in stages[a["stage"]]["all"]
                           if not eval_condition(c, ctx)[0]]
                a.update(state="FAIL", text=f"FAIL — timeout after {float(tmo):g} s; not met: " + "; ".join(pending),
                         t_utc=self._utc())
                self._disarm()
                self._changed(d, rec, "AUTO_TIMEOUT", a["text"])
                return
            if advanced:
                a["text"] = stages[a["stage"]].get("text") or f"stage {a['stage'] + 1} of {len(stages)} …"
                self._changed(d, rec, "AUTO_STAGE", a["text"])
                return
            # SWD-P3-03: while waiting, show what the board reports for the conditions not met yet
            base = stg.get("text") or f"stage {a['stage'] + 1} of {len(stages)} …"
            unmet = [cond_text(c, o) for c, (ok, o) in zip(stg["all"], res, strict=True) if not ok]
            txt = base + (" — observed: " + "; ".join(unmet) if unmet else "")
            if txt != a["text"]:                                 # display only: not a reason to rewrite the file
                a["text"] = txt
                self._pending_pub.append(("bringup.step", d.id))

    def _changed(self, d: StepDef, rec: dict[str, Any], what: str, text: str, **extra: Any) -> None:
        """Under the lock: history entry, state resolution, persistence + publication scheduled."""
        old = rec["state"]
        rec["state"] = resolve_state(d, rec)
        rec["origin"] = step_origin(d, rec)
        rec["updated_utc"] = self._utc()
        h = {"t_utc": rec["updated_utc"], "what": what}
        if text:
            h["text"] = text
        h.update(extra)
        if rec["state"] != old:
            h["state"] = rec["state"]
        rec["history"].append(h)
        self._q.append(("step", {"id": d.id, **h}))
        self._dirty = True
        self._pending_pub.append(("bringup.step", d.id))

    # ------------------------------------------------------------------------------------------ publication
    def _publish_state(self) -> None:
        with self._lock:
            self._pending_pub.append(("bringup.state", None))
        self._flush_publish()

    def _flush_publish(self) -> None:
        with self._lock:
            pend, self._pending_pub = self._pending_pub, []
        if not pend:
            return
        sent: set[tuple[str, Any]] = set()
        for topic, sid in pend:
            if (topic, sid) in sent:
                continue
            sent.add((topic, sid))
            try:
                payload = self.status() if topic == "bringup.state" else self.step(sid)
            except KeyError:
                continue
            self._be.events.publish(topic, payload)
        if ("bringup.state", None) not in sent:
            self._be.events.publish("bringup.state", self.status())

    # ------------------------------------------------------------------------------------------ operator
    def _mismatch_text(self) -> str | None:
        mm = (self._session or {}).get("board_mismatch")
        if not mm:
            return None
        return (f"BOARD CHANGED: this session belongs to board {mm.get('session_uid')}, board "
                f"{mm.get('connected_uid')} is connected — start a new session for this board, or create the "
                "package of the old session")

    def _no_session(self) -> GateResult | None:
        s = self._session
        if s is None or s.get("closed_utc") is not None:
            return GateResult((GateItem("NO_SESSION", Severity.REFUSE,
                                        "no open bring-up session — press [Start bring-up session…]"),))
        txt = self._mismatch_text()
        if txt is not None:
            return GateResult((GateItem("BOARD_CHANGED", Severity.REFUSE, txt),))
        return None

    def select(self, step_id: str) -> None:
        """The step shown in the assistant (arms its automatic check). Never moves anything."""
        if self.defs is None or step_id not in self.defs.ids:
            return
        with self._lock:
            s = self._session
            if s is None or s.get("closed_utc") is not None or s.get("current") == step_id:
                return
            prev = s.get("current")
            if prev in self.defs.ids:
                pr = self._rec(prev)
                if pr["auto"]["state"] == "WAITING":
                    pr["auto"].update(state="NA", text="not evaluated (step left)")
                    self._changed(self.defs.step(prev), pr, "AUTO_DISARMED", "")
            s["current"] = step_id
            self._session_log("SELECT", step_id)
        self._save()
        self._arm_current()
        self._publish_state()
        self._flush_publish()

    def submit(self, step_id: str, entries: Mapping[str, Any], remark: str = "") -> list[Issue]:
        """Operator entries (SW-BRU-001): ERROR issues → nothing stored; limits decide PASS / FAIL."""
        g = self._no_session()
        if g is not None:
            return [Issue(None, IssueSeverity.ERROR, g.items[0].code, g.text())]
        assert self.defs is not None
        try:
            d = self.defs.step(step_id)
        except KeyError:
            return [Issue(None, IssueSeverity.ERROR, "UNKNOWN_STEP", f"no step {step_id!r}")]
        with self._lock:
            others = {sid: dict(r.get("entries") or {}) for sid, r in (self._session or {}).get("steps", {}).items()}
        issues, result = check_entries(d.fields, entries, others)
        if any(i.severity == IssueSeverity.ERROR for i in issues):
            return issues
        with self._lock:
            rec = self._rec(step_id)
            rec["entries"] = {k: jsonable(v) for k, v in entries.items()}
            rec["entries_result"] = result
            rec["remark"] = str(remark or "")
            if rec["manual"] and rec["state"] != "SKIPPED":
                rec["manual"] = False
            self._changed(d, rec, "ENTRIES", "", entries=rec["entries"], result=result,
                          issues=[i.text for i in issues])
        self._save()
        self._flush_publish()
        return issues

    def mark(self, step_id: str, state: str, *, reason: str = "", remark: str = "",
             origin: str = "operator") -> GateResult:
        """Operator verdict: PASS / FAIL / SKIPPED (reason required) / NOT_DONE (reset: re-evaluate). ``origin``
        (``MARK_ORIGINS``) is recorded with the state and shown in the package (``cli-test`` in red)."""
        g = self._no_session()
        if g is not None:
            return g
        assert self.defs is not None
        if origin not in MARK_ORIGINS:
            return GateResult((GateItem("BAD_ORIGIN", Severity.REFUSE, f"unknown origin {origin!r}"),))
        if state not in STATES:
            return GateResult((GateItem("BAD_STATE", Severity.REFUSE, f"unknown state {state!r}"),))
        try:
            d = self.defs.step(step_id)
        except KeyError:
            return GateResult((GateItem("UNKNOWN_STEP", Severity.REFUSE, f"no step {step_id!r}"),))
        if state == "SKIPPED" and not str(reason).strip():
            return GateResult((GateItem("REASON_REQUIRED", Severity.REFUSE, "a reason is required to skip a step"),))
        with self._lock:
            pr = self._pulse
            if pr is not None and not pr.ended and pr.step_id == step_id:
                return GateResult((GateItem("OPERATION_RUNNING", Severity.REFUSE,
                                            "the pulse-integrity check of this step is running"),))
            rec = self._rec(step_id)
            if remark:
                rec["remark"] = str(remark)
            if state == "NOT_DONE":
                rec.update(manual=False, skip_reason="", entries={}, entries_result=None, mark_origin="")
                rec["auto"].update(state="NA", text="", stage=0, observed=[], t_utc=None)
                if rec["action"]["state"] != "RUNNING":
                    rec["action"].update(state=None, text="", result={})
                self._changed(d, rec, "RESET", str(reason or ""), origin=origin)
            else:
                rec.update(manual=True, state=state, skip_reason=str(reason) if state == "SKIPPED" else "",
                           mark_origin=origin)
                self._changed(d, rec, "MARK", str(reason or ""), state=state, origin=origin)
            rearm = state == "NOT_DONE" and (self._session or {}).get("current") == step_id
        self._save()
        if rearm:
            self._arm_current()
        self._flush_publish()
        return GATE_OK

    # ------------------------------------------------------------------------------------------ wizards
    def _on_wizard(self, wizard: str, st: Any) -> None:
        """Under the lock: a wizard state for the selected wizard step (opened through the normal GUI handler)."""
        s = self._session
        if s is None or self.defs is None or s.get("board_mismatch"):
            return
        try:
            d = self.defs.step(s.get("current"))
        except KeyError:
            return
        if d.wizard != wizard:
            return
        rec = self._rec(d.id)
        act = rec["action"]
        phase = str(getattr(st, "phase", ""))
        if phase in ("IDLE", "") or phase == act.get("phase"):
            return
        act["phase"] = phase
        if phase == "DONE":
            act.update(state="PASS", text=f"{wizard} done", result={"result": jsonable(getattr(st, "result", None))})
            self._changed(d, rec, "WIZARD_DONE", wizard)
        elif phase in ("ABORTED", "REFUSED"):
            why = getattr(st, "abort_reason", None) or "; ".join(getattr(st, "errors", ()) or ()) or phase
            act.update(state="FAIL", text=f"{wizard} {phase.lower()}: {why}")
            self._changed(d, rec, "WIZARD_" + phase, str(why))
        elif act.get("state") != "RUNNING":
            act.update(state="RUNNING", text=f"{wizard}: {phase}")
            self._changed(d, rec, "WIZARD_RUNNING", phase)
        else:
            act["text"] = f"{wizard}: {phase}"
            self._pending_pub.append(("bringup.step", d.id))

    # ------------------------------------------------------------------------------------------ recordings
    def _wants_recording(self, flags: int) -> bool:
        s = self._session
        if s is None or self.defs is None or self._rec_own is not None:
            return False
        try:
            d = self.defs.step(s.get("current"))
        except KeyError:
            return False
        return d.motion and bool(flags & (1 << pg.DATA_FLAGS_BITS.index("MOVING")))

    def _recording_tick(self, flags: int) -> None:
        moving = bool(flags & (1 << pg.DATA_FLAGS_BITS.index("MOVING")))
        with self._lock:
            s = self._session
            if s is None:
                return
            cur = s.get("current")
            if self._rec_own is None:
                if self._rec_skip_step is not None and self._rec_skip_step != cur:
                    self._rec_skip_step = None
                if self._wants_recording(flags) and self._rec_skip_step != cur:
                    self._rec_req = "start:" + str(cur)
            else:
                rec_st = self._rec(self._rec_step) if self._rec_step else None
                left = cur != self._rec_step or s.get("closed_utc") is not None
                resolved = rec_st is not None and rec_st["state"] != "NOT_DONE" and \
                    rec_st["action"]["state"] != "RUNNING"
                if (left or resolved) and not moving and (self._pulse is None or self._pulse.ended):
                    self._rec_req = "stop"
        if self._rec_req is not None:
            self._wake.set()

    def _record_action(self, req: str) -> None:
        """Writer context: start / stop the recording of a motion step (SW-BRU-002)."""
        be = self._be
        if req.startswith("start:"):
            step_id = req[6:]
            with self._lock:
                if self._rec_own is not None:
                    return
            if be.recorder.state == "RECORDING":
                with self._lock:                           # the operator records already: not ours
                    if self._rec_skip_step != step_id:
                        self._rec_skip_step = step_id
                        self._session_log("RECORDING_NOT_STARTED", f"{step_id}: a recording is already running")
                        self._dirty = True
                return
            g = be.record_start_with({"bringup": {"session_id": (self._session or {}).get("session_id"),
                                                  "step": step_id}})
            folder = be.recorder.folder
            with self._lock:
                if not g.ok or folder is None:
                    self._rec_skip_step = step_id
                    self._session_log("RECORDING_NOT_STARTED", f"{step_id}: {g.text()}")
                    self._dirty = True
                    return
                self._rec_own, self._rec_step = str(folder), step_id
                root = str(Path(folder).parent)
                self._own_roots.add(Path(root))
                if self._session is not None:
                    self._session["recordings"].append({"step": step_id, "folder": str(folder), "root": root,
                                                        "t_utc": self._utc()})
                    self._rec(step_id)["recordings"].append(str(folder))
                    self._session_log("RECORDING_START", f"{step_id}: {folder}")
                    self._dirty = True
        elif req == "stop":
            with self._lock:
                own, self._rec_own = self._rec_own, None
                step, self._rec_step = self._rec_step, None
            if own is not None and be.recorder.folder is not None and str(be.recorder.folder) == own \
                    and be.recorder.state == "RECORDING":
                be.record_stop()
            with self._lock:
                self._session_log("RECORDING_STOP", f"{step}: {own}")
                self._dirty = True

    # ------------------------------------------------------------------------------------------ engine action
    def run_action(self, step_id: str, *, confirmed: bool = False) -> GateResult:
        """Engine-run step action (``pulse_integrity``) after the operator's click (SW-BRU-004): returns the normal
        motion gate unchanged on a refusal; CONFIRM first (full-speed moves over the stroke)."""
        g = self._no_session()
        if g is not None:
            return g
        assert self.defs is not None
        try:
            d = self.defs.step(step_id)
        except KeyError:
            return GateResult((GateItem("UNKNOWN_STEP", Severity.REFUSE, f"no step {step_id!r}"),))
        if not d.engine_action:
            return GateResult((GateItem("NOT_AN_ENGINE_ACTION", Severity.REFUSE,
                                        f"step {step_id}: action {d.action!r} runs through the normal GUI control"),))
        return self._pulse_start(d, confirmed)

    def cancel_action(self) -> Any:
        """Abort the running engine action: controlled STOP through the backend (normal STOP path)."""
        with self._lock:
            pr = self._pulse
            if pr is None or pr.ended:
                return None
            self._pulse_end(pr, "ABORTED", "cancelled by the operator", cause="CANCEL")
        try:
            return self._be.stop("bringup")
        except Exception:  # noqa: BLE001
            log.exception("bring-up cancel STOP failed")
            return None

    # ------------------------------------------------------------------------------------------ pulse integrity
    def _pulse_start(self, d: StepDef, confirmed: bool) -> GateResult:
        be = self._be
        p = d.params
        with self._lock:
            if self._pulse is not None and not self._pulse.ended:
                return GateResult((GateItem("OPERATION_RUNNING", Severity.REFUSE,
                                            "the pulse-integrity check is running"),))
            rec = self._rec(d.id)
            if rec["action"].get("hard_fail"):
                return GateResult((GateItem("STOP_REPORTED", Severity.REFUSE,
                                            "the pulse-integrity check failed with a stop condition — stop the "
                                            "bring-up and report (a new session is needed after the fix)"),))
            if rec["action"].get("verdict_fails", 0) > int(p["repeats_allowed"]):
                return GateResult((GateItem("REPEAT_EXHAUSTED", Severity.REFUSE,
                                            f"{p['fail_text']} — no further repeat of this check in this session"),))
        v = float(p["speed_mm_s"])                                 # exactly this speed, never faster (item 1)
        a, b = float(p["from_mm"]), float(p["to_mm"])
        gm = be.motion.check(MotionKind.MOVE, speed_mm_s=None, target_mm=b)    # state gates, identical to the API
        if gm.ok:
            gm = be.motion.check(MotionKind.MOVE, speed_mm_s=None, target_mm=a)
        if not gm.ok:
            return gm
        lim = be.motion.limits()
        if lim is None:
            return GateResult((GateItem("LIMITS_UNKNOWN", Severity.REFUSE, "motion limits not known yet"),))
        caps = [float(lim.v_cap_mm_s), float(lim.v_travel_mm_s)] + \
            ([float(lim.v_cap_fw_mm_s)] if lim.v_cap_fw_mm_s is not None else [])
        cap = min(caps)
        if v > cap + 1e-9:                                         # refuse, no FAIL, no limit raised (item 1)
            return GateResult((GateItem("SPEED_BELOW_QUALIFICATION", Severity.REFUSE,
                                        f"the qualification needs {v:g} mm/s; the PC/FW speed limit is {cap:g} mm/s "
                                        "— no limit is raised by the assistant"),))
        gm = be.motion.check(MotionKind.MOVE, speed_mm_s=v, target_mm=b)
        if gm.ok:
            gm = be.motion.check(MotionKind.MOVE, speed_mm_s=v, target_mm=a)
        if not gm.ok:
            return gm
        gh = be.motion.check(MotionKind.HOME)
        if not gh.ok:
            return gh
        if not confirmed:
            n = int(p["round_trips"])
            item = GateItem("BRINGUP_MOTION", Severity.CONFIRM,
                            f"pulse-integrity check: {int(p['baseline_homes'])} re-homes, {n} round trips "
                            f"{a:g} ↔ {b:g} mm at {v:g} mm/s, final re-home — nothing in the travel, no specimen")
            return GateResult(gh.items + gm.items + (item,))
        ops: list[tuple[str, Any]] = [("home", "baseline")] * int(p["baseline_homes"])
        for _ in range(int(p["round_trips"])):
            ops += [("move", b), ("move", a)]
        ops.append(("home", "final"))
        pr = _Pulse(d.id, dict(p), True, v, ops, self._utc())
        with self._lock:
            self._pulse = pr
            rec = self._rec(d.id)
            rec["action"].update(state="RUNNING", text="baseline re-home 1 …", result={})
            self._changed(d, rec, "PULSE_START", f"v={v:g} mm/s {a:g}↔{b:g} mm")
            if self._rec_own is None and be.recorder.state != "RECORDING":
                self._rec_req = "start:" + d.id
        self._save()
        if be.lockstep:
            self._drain()
        else:
            self._wake.set()
        self._pulse_next(pr)
        self._flush_publish()
        return GATE_OK

    def _pulse_def(self, pr: _Pulse) -> tuple[StepDef, dict[str, Any]]:
        assert self.defs is not None
        return self.defs.step(pr.step_id), self._rec(pr.step_id)

    def _pulse_next(self, pr: _Pulse) -> None:
        be = self._be
        with self._lock:
            if pr.ended or self._pulse is not pr:
                return
            if pr.idx >= len(pr.ops):
                self._pulse_evaluate(pr)
                return
            op, arg = pr.ops[pr.idx]
            d, rec = self._pulse_def(pr)
            if op == "home":
                now = self._now_ns()
                fresh = self._board_fresh(now - FRESH_NS)          # item 8: fresh data after the last connect / BOOT
                homed = fresh and bool(be.device.latest()[0] & (1 << pg.DATA_FLAGS_BITS.index("HOMED")))
                if not homed:                              # E's rule (b): d only from a HOME issued while HOMED = 1
                    why = "axis not homed" if fresh else "HOMED not confirmed by fresh board data"
                    self._pulse_end(pr, "ABORTED", f"{why} when the re-home was issued — reading discarded; "
                                    "home the axis and run the check again from the baseline")
                    return
                pr.home_issued, pr.home_ticket_done, pr.homed_value, pr.home_ticket_ns = True, False, None, None
                k = len(pr.d0) + 1
                rec["action"]["text"] = (f"baseline re-home {k} of {pr.p['baseline_homes']} …" if arg == "baseline"
                                         else "final re-home …")
                self._pending_pub.append(("bringup.step", pr.step_id))
        if op == "home":
            t = be.motion.home(load_confirmed=pr.confirmed)
            t.add_done_callback(lambda f: self._pulse_home_done(pr, f))
        else:
            with self._lock:
                d, rec = self._pulse_def(pr)
                rec["action"]["text"] = (f"round trip {pr.moves_done // 2 + 1} of {pr.p['round_trips']}: → {arg:g} mm")
                self._pending_pub.append(("bringup.step", pr.step_id))
            t = be.motion.move_to(float(arg), speed_mm_s=pr.v_mm_s)
            t.add_done_callback(lambda f: self._pulse_move_done(pr, f))

    def _refusal(self, exc: BaseException) -> tuple[str, bool]:
        """(raw text, is a speed / accel cap refusal)."""
        if isinstance(exc, (GateRefused, ConfirmationRequired)):
            gate = getattr(exc, "gate", None)
            codes = gate.codes() if gate is not None else ()
            return (gate.text() if gate is not None else str(exc)), bool({"SPEED_CAP", "ACCEL_CAP"} & set(codes))
        if isinstance(exc, NackError):
            return f"NACK {exc.name} detail {exc.detail} ({exc.detail_text})", exc.name == "E_RANGE"
        return str(exc), False

    def _pulse_move_done(self, pr: _Pulse, f: Future) -> None:
        exc = f.exception()
        with self._lock:
            if pr.ended:
                return
            if exc is not None:
                raw, cap = self._refusal(exc)
                pr.raw.append(raw)
                if cap:
                    self._pulse_end(pr, "FAIL", "FAIL — E_RANGE refusal (no limit is raised): " + raw,
                                    verdict="E_RANGE", hard=True)
                else:
                    self._pulse_end(pr, "ABORTED", "move refused: " + raw)
                return
            out = f.result()
            if out.kind == "REFUSED" and getattr(out, "nack", None) == "E_RANGE":        # item 11
                raw = f"NACK E_RANGE ({out.text})"
                pr.raw.append(raw)
                self._pulse_end(pr, "FAIL", "FAIL — E_RANGE refusal (no limit is raised): " + raw, verdict="E_RANGE",
                                hard=True)
                return
            if out.kind != "DONE" or out.done is None or out.done.reason != "TARGET":
                why = out.text or (out.done.reason if out.done is not None else out.kind)
                if out.done is not None and out.done.stop_cause:
                    why = f"{out.done.reason} ({out.done.stop_cause})"
                if out.done is not None and "LIMIT" in str(out.done.stop_cause or ""):
                    self._pulse_end(pr, "FAIL", f"FAIL — move stopped by a limit switch: {why}", verdict="LIMIT",
                                    hard=True)
                    return
                self._pulse_end(pr, "ABORTED", f"move ended: {why} — run the check again from the baseline",
                                cause="MOVE")
                return
            pr.moves_done += 1
            pr.idx += 1
        self._pulse_next(pr)

    def _pulse_home_done(self, pr: _Pulse, f: Future) -> None:
        exc = f.exception()
        with self._lock:
            if pr.ended:
                return
            if exc is not None:
                raw, _cap = self._refusal(exc)
                pr.raw.append(raw)
                self._pulse_end(pr, "ABORTED", "re-home refused: " + raw)
                return
            out = f.result()
            if out.kind != "DONE":
                self._pulse_end(pr, "ABORTED", f"re-home ended: {out.text or out.kind}", cause="HOME")
                return
            pr.home_ticket_done = True
            pr.home_ticket_ns = self._now_ns()
            ready = pr.homed_value is not None
        if ready:
            self._pulse_home_complete(pr)

    def _pulse_on_fw_event(self, ev: Any, t_ns: int = 0) -> None:
        """Under the lock (observer thread): HOMED values, HOME_DRIFT, and the disturbance classes of
        ``PULSE_DISTURB`` / ``PULSE_FAIL_FAULTS`` (E's review (b), B-BRU-04 items 2 and 10)."""
        name = getattr(ev, "name", "")
        pr = self._pulse
        if pr is None or pr.ended:
            last = self._pulse_last
            if name == "BOOT" and last is not None and last[2] in ("LINK", "MOVE", "HOME")                     and t_ns - last[1] <= BOOT_AFTER_ABORT_NS:
                self._pulse_boot_after_abort(last[0])
            return
        op = pr.ops[pr.idx][1] if pr.idx < len(pr.ops) and pr.ops[pr.idx][0] == "home" else "move"
        arg = getattr(ev, "arg_name", None) or str(getattr(ev, "arg", ""))
        if name == "FAULT_SET" and arg == "HOME_DRIFT":
            if op == "baseline":                                 # item 10: switch mounting, not counted
                self._pulse_end(pr, "FAIL", f"FAIL — HOME_DRIFT during the baseline (deviation {ev.value} µm): "
                                f"{pr.p['baseline_fail_text']}", verdict="BASELINE_SCATTER")
            else:
                pr.drift_fault = True
            return
        if name == "HOMED" and pr.home_issued:
            pr.homed_value = int(ev.value)            # completed by the ticket callback or the tick
            return
        if name == "FAULT_SET":
            if arg in PULSE_FAIL_FAULTS:
                self._pulse_end(pr, "FAIL", f"FAIL — fault {arg} during the check", verdict="FAULT", hard=True)
            else:
                self._pulse_disturb(f"fault {arg}")
            return
        cls = PULSE_DISTURB.get(name)
        if cls is None:
            return
        if name == "ALM_CHANGED" and int(getattr(ev, "arg", 0)) != 1:
            return
        if name == "LIMIT_SET" and op != "move":
            return                                                 # homing runs onto the START switch
        if cls[0] == "FAIL":
            self._pulse_end(pr, "FAIL", f"FAIL — {cls[1]} ({name} {arg})", verdict=cls[2], hard=True)
        else:
            self._pulse_disturb(f"{cls[1]} ({name})")

    def _pulse_disturb(self, why: str, kind: str = "EVENT") -> None:
        pr = self._pulse
        if pr is None or pr.ended:
            return
        self._pulse_end(pr, "ABORTED", f"{why} during the check — readings discarded; clear the cause, enable, home "
                        "and run the check again from the baseline", cause=kind)

    def _pulse_boot_after_abort(self, pr: _Pulse) -> None:
        """Under the lock: the abort (link loss, move / re-home ended by the reset) was a board restart → the attempt
        becomes the BOOT FAIL (B-BRU-04 item 2)."""
        self._pulse_last = None
        d, rec = self._pulse_def(pr)
        act = rec["action"]
        if not act["attempts"] or act["attempts"][-1].get("state") != "ABORTED":
            return
        text = "FAIL — unexpected board restart (BOOT) during the check — STOP the bring-up and report"
        act["attempts"][-1].update(state="FAIL", verdict="BOOT", text=text)
        act["result"] = dict(act.get("result") or {}, verdict="BOOT")
        act.update(state="FAIL", text=text, hard_fail=True)
        self._changed(d, rec, "PULSE_FAIL", text, verdict="BOOT")
        self._dirty = True
        self._wake.set()

    def _pulse_home_complete(self, pr: _Pulse) -> None:
        with self._lock:
            if pr.ended or not pr.home_issued or pr.homed_value is None or not pr.home_ticket_done:
                return
            op, arg = pr.ops[pr.idx]
            d_um = int(pr.homed_value)
            pr.home_issued = False
            if arg == "baseline":
                pr.d0.append(d_um)
                if len(pr.d0) == int(pr.p["baseline_homes"]):
                    scatter = max(abs(x) for x in pr.d0)
                    if pr.drift_fault:
                        self._pulse_end(pr, "FAIL", "FAIL — HOME_DRIFT during the baseline: " + str(pr.p["fail_text"]),
                                        verdict="DRIFT")
                        return
                    if scatter > float(pr.p["baseline_max_um"]):
                        self._pulse_end(pr, "FAIL", f"FAIL — baseline scatter max|d0| = {scatter} µm > "
                                        f"{pr.p['baseline_max_um']} µm: {pr.p['baseline_fail_text']}",
                                        verdict="BASELINE_SCATTER")
                        return
            else:
                pr.d1 = d_um
            pr.idx += 1
        self._pulse_next(pr)

    def _pulse_tick(self, now: int) -> None:
        with self._lock:
            pr = self._pulse
            if pr is None or pr.ended or not pr.home_issued or not pr.home_ticket_done or pr.home_ticket_ns is None:
                return
            complete = pr.homed_value is not None
            if not complete and now - pr.home_ticket_ns > float(pr.p["home_event_timeout_s"]) * 1e9:
                self._pulse_end(pr, "ABORTED", "EVENT HOMED not received after the re-home — reading discarded")
        if complete:
            self._pulse_home_complete(pr)

    def _pulse_evaluate(self, pr: _Pulse) -> None:
        """Under the lock: pass if no HOME_DRIFT and |d1| ≤ max(d_floor, d_factor · max|d0ᵢ|) (E's review (c))."""
        p = pr.p
        scatter = max(abs(x) for x in pr.d0) if pr.d0 else 0
        limit = max(float(p["d_floor_um"]), float(p["d_factor"]) * scatter)
        d1 = pr.d1
        if pr.drift_fault or self._fault_bit("HOME_DRIFT"):
            self._pulse_end(pr, "FAIL", f"FAIL — HOME_DRIFT latched (d1 = {d1} µm)", verdict="DRIFT", limit=limit)
        elif d1 is None or abs(d1) > limit:
            self._pulse_end(pr, "FAIL", f"FAIL — |d1| = {None if d1 is None else abs(d1)} µm > {limit:g} µm",
                            verdict="DRIFT", limit=limit)
        else:
            self._pulse_end(pr, "PASS", f"PASS — |d1| = {abs(d1)} µm ≤ {limit:g} µm (d0 = {pr.d0} µm, "
                            f"{pr.moves_done} moves at {pr.v_mm_s:g} mm/s, no HOME_DRIFT)", verdict="PASS", limit=limit)

    def _fault_bit(self, name: str) -> bool:
        b = self._be.device.board
        return b is not None and bool(int(b.faults) >> pg.FAULTS_BITS.index(name) & 1)

    def _pulse_end(self, pr: _Pulse, state: str, text: str, *, verdict: str | None = None,
                   limit: float | None = None, hard: bool = False, cause: str = "") -> None:
        """Under the lock: record the attempt; a counted FAIL (DRIFT) allows ``repeats_allowed`` repeats; a ``hard``
        FAIL (limit, STEP_FAULT / LOAD_LIMIT / AFE_FAULT, BOOT, E_RANGE) stops the check for this session."""
        if pr.ended:
            return
        pr.ended = True
        self._pulse_last = (pr, self._now_ns(), cause) if state == "ABORTED" else None
        d, rec = self._pulse_def(pr)
        act = rec["action"]
        if verdict == "DRIFT":
            act["verdict_fails"] = int(act.get("verdict_fails", 0)) + 1
            left = int(pr.p["repeats_allowed"]) - act["verdict_fails"] + 1
            text += (f" — one repeat allowed ({left} left)" if left > 0 else f" — {pr.p['fail_text']}")
        if hard:
            act["hard_fail"] = True
            text += " — STOP the bring-up and report"
        result = {"verdict": verdict or state, "d0_um": list(pr.d0), "d1_um": pr.d1, "limit_um": limit,
                  "speed_mm_s": pr.v_mm_s, "moves_done": pr.moves_done, "home_drift": pr.drift_fault,
                  "raw": list(pr.raw), "from_mm": pr.p["from_mm"], "to_mm": pr.p["to_mm"],
                  "round_trips": pr.p["round_trips"], "baseline_homes": pr.p["baseline_homes"]}
        act["attempts"].append({"t_start_utc": pr.started_utc, "t_end_utc": self._utc(), "state": state,
                                "text": text, **result})
        act.update(state=state, text=text, result=result)
        self._changed(d, rec, "PULSE_" + state, text, verdict=verdict or state)
        self._dirty = True
        self._wake.set()

    # ------------------------------------------------------------------------------------------ package
    def create_package_async(self) -> Future:
        """SW-BRU-003: the zip of the current (or the last) session; partial at any point. Future[BuPackage]."""
        be = self._be
        with self._lock:
            d = self._dir
            open_ = self._session is not None and self._session.get("closed_utc") is None
        if d is None:
            try:
                d = find_session(self.data_dir, None)
            except FileNotFoundError as exc:
                f: Future = Future()
                f.set_exception(exc)
                return f
            open_ = False

        def job() -> Any:
            if open_:
                self._dump_params("package")
                self._snapshot()
            self.flush()
            roots = [*app_recording_roots(self.data_dir), Path(be._recordings_root()), *self._own_roots]  # noqa: SLF001
            pkg = build_package(d, self.out_dir(), data_dir=self.data_dir, defs=None, info=be.device.info,
                                now=_dt.datetime.fromtimestamp(be.clock.wall_ns() / 1e9, tz=_dt.UTC),
                                recording_roots=roots)
            with self._lock:
                self.last_package = pkg.path
                if self._session is not None and self._dir == d:
                    self._session_log("PACKAGE", pkg.path)
                    self._dirty = True
            self.flush()
            self._publish_state()
            return pkg
            yield  # pragma: no cover - generator job

        return be._report_job(job)  # noqa: SLF001


__all__ = [
    "BuField", "BuAuto", "BuStep", "BuSection", "BuStatus", "BuPackage", "BringupEngine", "Definitions", "StepDef",
    "EvalContext", "load_definitions", "parse_definitions", "check_definitions", "eval_condition", "apply_op",
    "check_entries", "resolve_state", "build_package", "verify_package", "collect_files", "build_record",
    "render_html", "package_name", "list_sessions", "find_session", "default_bringup_root", "resource_path",
    "versions", "jsonable", "KINDS", "GUI_ACTIONS", "ENGINE_ACTIONS", "SHOW_TABS",
]

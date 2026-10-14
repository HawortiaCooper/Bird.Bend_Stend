"""Gate approval file (D-57 b): the repo-free form of the D-06 interlock of the gate runner.

Owner: Validator E (00_System/tools/hil, the Integrator reviews). Spec: GATE_RUNNER.md §3.

The Orchestrator generates the file with `make_approval.py` from the dedicated `D-06-GATE-YYYYMMDD[-TAG]` row of
DECISIONS.md / STATUS.md (the C-R1…C-R4 row rules of hil_link). The runner checks it completely BEFORE it opens any
port; every mismatch refuses (InterlockError). The file is not secret: integrity is `body_sha256` (detects any edit);
authenticity is checked offline — the Orchestrator records the body hash in the D-06-GATE row, Validator E's
analysis tool compares the hash in the results zip with it.

    {"schema": "bird.bend.gate_approval", "schema_version": 1,
     "reference": "D-06-GATE-20261020", "decision_row": "| D-06-GATE-20261020 | … | approved (PO) |",
     "board_uid": "<24 hex>", "valid_from": "2026-10-20", "valid_until": "2026-10-27",
     "kit": {"name": "...", "commit": "<git>", "fw_version": "0.8.6", "param_dict_hash": "0x…",
             "images": {"release":  {"path": "BringUp_FW…/firmware/….hex", "sha256": "…", "build": "YYYYMMDD-HHMM"},
                        "meas":     {"path": "GateKit_FW…/…_HW_MEAS_GATE-ONLY.hex", "sha256": "…", "build": "…-MEAS"},
                        "meas_dwt": {"path": "GateKit_FW…/…_HW_MEAS_DWT_GATE-ONLY.hex", "sha256": "…", "build": "…-DWT"}}},
     "runner": {"name": "BirdBendStand-GateRunner", "version": "1.0.0", "sha256": "…"},
     "accepted_open": [{"item": "HG-12", "ref": "RR-HG-12-01", "row": "| RR-HG-12-01 | … | accepted (PO) |"}],
     "evidence": {"tc_sys_009_02": "PASS — …"},
     "options": {"buffer_fitted": true},          (optional, only when the Orchestrator pins a session option)
     "generated_by": {"tool": "make_approval.py", "version": "1.0", "utc": "…", "repo_commit": "…", "by": "…"},
     "body_sha256": "<SHA-256 of the canonical JSON of every other member>"}

Verifies: SYS-009, D-06, D-57 (b)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import gate_res
import hil_link as hl
from hil_link import InterlockError

SCHEMA = "bird.bend.gate_approval"
SCHEMA_VERSION = 1
MAX_SPAN_DAYS = 7                       # as the repo interlock (max_age_days)
IMAGES = ("release", "meas", "meas_dwt")
#: session options a board session may use only if the approval file pins them (C-GR-1); defaults = the CLI defaults
OPTION_TYPES = {"quick": bool, "seed": int, "counts_per_n": float, "buffer_fitted": bool, "power_sense_fitted": bool}
DEFAULT_OPTIONS = {"quick": False, "seed": 20261005, "counts_per_n": None, "buffer_fitted": False,
                   "power_sense_fitted": False}


def parse_option(text: str) -> tuple[str, Any]:
    """'key=value' of make_approval --option (typed per OPTION_TYPES)."""
    k, _, v = text.partition("=")
    k, v = k.strip(), v.strip()
    if k not in OPTION_TYPES or not v:
        raise InterlockError(f"option {text!r}: expected one of {sorted(OPTION_TYPES)}=value")
    t = OPTION_TYPES[k]
    if t is bool:
        if v.lower() not in ("true", "false", "1", "0", "yes", "no"):
            raise InterlockError(f"option {k}: {v!r} is not a boolean")
        return k, v.lower() in ("true", "1", "yes")
    try:
        return k, t(v)
    except ValueError:
        raise InterlockError(f"option {k}: {v!r} is not a {t.__name__}") from None
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
HG_RE = re.compile(r"^HG-\d{2}[a-z]{0,2}$")


def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def body_sha256(doc: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in doc.items() if k != "body_sha256"})).hexdigest()


def seal(doc: dict) -> dict:
    """Return the document with its `body_sha256` (make_approval / tests)."""
    d = {k: v for k, v in doc.items() if k != "body_sha256"}
    d["body_sha256"] = body_sha256(d)
    return d


@dataclass
class Approval:
    reference: str
    board_uid: str
    valid_from: _dt.date
    valid_until: _dt.date
    kit: dict
    runner: dict
    accepted_open: dict[str, str]
    evidence: dict
    body_sha256: str
    path: str
    doc: dict = field(repr=False, default_factory=dict)
    options: dict = field(default_factory=dict)

    def builds(self) -> dict[str, str]:
        return {k: str(v.get("build", "")) for k, v in self.kit.get("images", {}).items()}

    def check_window(self, today: _dt.date | None = None) -> None:
        """D-06 at every port (re)open: today inside [valid_from, valid_until]."""
        today = today or _dt.date.today()
        if today < self.valid_from:
            raise InterlockError(f"D-06: approval {self.reference} is valid from {self.valid_from} (today {today})")
        if today > self.valid_until:
            raise InterlockError(f"D-06: approval {self.reference} expired on {self.valid_until} (today {today}) — "
                                 "ask the PO for a new approval file")


def _req(doc: dict, key: str, typ: type | tuple) -> Any:
    if key not in doc:
        raise InterlockError(f"approval file: member {key!r} missing")
    v = doc[key]
    if not isinstance(v, typ):
        raise InterlockError(f"approval file: member {key!r} has the wrong type")
    return v


def _date(s: Any, key: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(str(s))
    except ValueError:
        raise InterlockError(f"approval file: {key} {s!r} is not a date YYYY-MM-DD") from None


def load(path: Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise InterlockError(f"D-06: approval file {p} not found — the port is NOT opened")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise InterlockError(f"approval file {p.name}: not readable JSON ({e})") from None
    if not isinstance(doc, dict):
        raise InterlockError(f"approval file {p.name}: not a JSON object")
    return doc


def check(path: Path, *, today: _dt.date | None = None, board_uid: str | None = None,
          delivery: Path | None = None, runner_sha: str | None = None, dict_hash: str | None = None) -> Approval:
    """Validate the approval file completely (D-57 b). Raises InterlockError on the first problem; the caller opens
    no port before this returned. `delivery` = folder holding the kits (paths in kit.images are relative to it);
    `runner_sha` defaults to this runner's identity, `dict_hash` to the bundled params.yaml."""
    doc = load(path)
    if doc.get("schema") != SCHEMA or doc.get("schema_version") != SCHEMA_VERSION:
        raise InterlockError(f"approval file: schema {doc.get('schema')!r} v{doc.get('schema_version')!r} is not "
                             f"{SCHEMA} v{SCHEMA_VERSION}")
    sha = _req(doc, "body_sha256", str)
    if sha != body_sha256(doc):
        raise InterlockError("approval file: body_sha256 does not match the content — the file was edited or "
                             "damaged; use the file generated by the Orchestrator")
    ref = _req(doc, "reference", str).strip()
    m = hl.APPROVAL_RE.match(ref)
    if not m:
        raise InterlockError(f"approval file: reference {ref!r} does not match D-06-GATE-YYYYMMDD[-TAG]")
    ref_date = _dt.date(int(m[1]), int(m[2]), int(m[3]))
    row = _req(doc, "decision_row", str)
    uid = _req(doc, "board_uid", str).strip().upper()
    if not hl.UID_RE.match(uid):
        raise InterlockError(f"approval file: board_uid {uid!r} is not 24 hex digits")
    if hl.decide_rows([row], hl.APPROVED_STATES, must_mention=uid) is None or (hl._cells(row) or [""])[0] != ref:
        raise InterlockError("approval file: decision_row is not a dedicated approved row for the reference that "
                             "names the board UID (C-R1…C-R4 row rules)")
    vf, vu = _date(_req(doc, "valid_from", str), "valid_from"), _date(_req(doc, "valid_until", str), "valid_until")
    if vf != ref_date:
        raise InterlockError(f"approval file: valid_from {vf} differs from the reference date {ref_date}")
    if vu < vf or (vu - vf).days > MAX_SPAN_DAYS:
        raise InterlockError(f"approval file: validity {vf}…{vu} is not a window of 0…{MAX_SPAN_DAYS} days")
    kit = _req(doc, "kit", dict)
    runner = _req(doc, "runner", dict)
    acc = _req(doc, "accepted_open", list)
    evidence = _req(doc, "evidence", dict)
    _req(doc, "generated_by", dict)
    opts = doc.get("options", {})
    if not isinstance(opts, dict) or set(opts) - set(OPTION_TYPES):
        raise InterlockError(f"approval file: options must be an object with keys from {sorted(OPTION_TYPES)}")
    for k, v in opts.items():
        t = OPTION_TYPES[k]
        if not (isinstance(v, t) and not (t is int and isinstance(v, bool))) and not (t is float and
                                                                                      isinstance(v, int)):
            raise InterlockError(f"approval file: option {k} has the wrong type")
    a = Approval(ref, uid, vf, vu, kit, runner, {}, evidence, sha, str(path), doc, dict(opts))
    a.check_window(today)
    if board_uid is not None and board_uid.strip().upper() != uid:
        raise InterlockError(f"approval file is for board {uid}, not for {board_uid.strip().upper()} — wrong board "
                             "or wrong file")
    # runner identity
    want_runner = runner_sha or gate_res.runner_identity()["sha256"]
    if str(runner.get("sha256", "")).lower() != want_runner:
        raise InterlockError(f"approval file: runner hash {str(runner.get('sha256'))[:16]}… is not this runner "
                             f"({want_runner[:16]}…) — use the runner of the gate kit named in the approval")
    # parameter dictionary of the kit = the runner's (bundled params.yaml)
    want_dict = dict_hash or hl.DICT_HASH
    if str(kit.get("param_dict_hash", "")).upper() != want_dict.upper():
        raise InterlockError(f"approval file: kit dictionary hash {kit.get('param_dict_hash')} != runner "
                             f"{want_dict}")
    imgs = kit.get("images")
    if not isinstance(imgs, dict) or set(imgs) != set(IMAGES):
        raise InterlockError(f"approval file: kit.images must name exactly {IMAGES}")
    for name, im in imgs.items():
        if not isinstance(im, dict) or not SHA_RE.match(str(im.get("sha256", ""))) or not im.get("path") or \
                not im.get("build"):
            raise InterlockError(f"approval file: kit.images.{name} needs path, sha256 and build")
        if not str(im["path"]).lower().endswith(".hex"):
            raise InterlockError(f"approval file: kit.images.{name} is not a .hex file (only .hex is flashed)")
        if delivery is not None:
            f = (Path(delivery) / str(im["path"])).resolve()
            if not f.is_file():
                raise InterlockError(f"kit file {im['path']} not found under {delivery}")
            got = gate_res.sha256_file(f)
            if got != im["sha256"]:
                raise InterlockError(f"kit file {im['path']}: SHA-256 {got[:16]}… differs from the approval "
                                     f"({im['sha256'][:16]}…) — not the approved image")
    # accepted-open items (§6.6), already validated against DECISIONS / STATUS by make_approval: format re-checked
    for e in acc:
        if not isinstance(e, dict):
            raise InterlockError("approval file: accepted_open entries must be objects")
        item, aref, arow = str(e.get("item", "")), str(e.get("ref", "")), str(e.get("row", ""))
        if not HG_RE.match(item) or not hl.OPEN_REF_RE.match(aref):
            raise InterlockError(f"approval file: accepted_open {item}:{aref} is not HG-xx:REF")
        if (hl._cells(arow) or [""])[0] != aref or hl.decide_rows([arow], hl.ACCEPTED_STATES, must_mention=item) is None:
            raise InterlockError(f"approval file: accepted_open {item}:{aref} has no accepted row naming {item}")
        a.accepted_open[item] = aref
    return a

"""Fake ``BringupAPI`` for the GUI tests (D-56 a, SRS SW-BRU-001…004) — the contract the GUI is built against until B
hands back the real engine (``core/bringup.py``, ``api.BringupAPI``). Proposed to B as GRQ-B-40 (SW_design_GUI §3.8).

Contract (duck-typed; the GUI reads attributes with ``getattr`` defaults):
* ``status() -> BuStatus``: active, session_id, board_uid, fw_build, started_utc, total / done / passed / failed /
  skipped, current (step id), folder (session folder).
* ``sections() -> list[BuSection(id, title, step_ids)]``; ``steps() -> list[BuStep]``; ``step(step_id) -> BuStep``.
* ``BuStep``: id (guide step id, e.g. "6.3"), section, title, instruction, expected, kind ("auto" | "operator" |
  "action" | "info"), fields (``BuField``: name, label, kind "confirm" | "number" | "text" | "choice", unit, min, max,
  decimals, choices, required), action (GUI action id or None), motion (bool), stop_if_fail (bool), safety (text),
  hg (gate overlap note), auto (``BuAuto``: state WAITING | PASS | FAIL | NA, text), state NOT_DONE | PASS | FAIL |
  SKIPPED, entries (dict), remark, skip_reason.
* ``start(*, confirmed=False) -> GateResult`` (CONFIRM when a session exists), ``select(step_id)``,
  ``submit(step_id, entries, remark="") -> list[Issue]`` (values outside the limits make a measure step FAIL; a
  missing required entry is an ERROR issue), ``mark(step_id, state, *, reason="", remark="") -> GateResult``
  (SKIPPED needs a reason), ``create_package_async() -> Future[BuPackage(path, size_bytes, files, send_to)]``.
"""
from __future__ import annotations

import dataclasses
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from bend_stand.core.api import GATE_OK, GateItem, GateResult, Issue, IssueSeverity, Severity


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
    required_when: dict | None = None   # B-BRU-08: {"field", "op", "value"}


@dataclass(frozen=True)
class BuAuto:
    state: str = "WAITING"          # WAITING | PASS | FAIL | NA
    text: str = ""


@dataclass(frozen=True)
class BuStep:
    id: str
    section: str
    title: str
    instruction: str
    expected: str
    kind: str = "operator"          # auto | operator | action | info
    fields: tuple[BuField, ...] = ()
    action: str | None = None
    motion: bool = False
    stop_if_fail: bool = False
    safety: str = ""
    hg: str = ""
    auto: BuAuto | None = None
    state: str = "NOT_DONE"         # NOT_DONE | PASS | FAIL | SKIPPED
    entries: dict = field(default_factory=dict)
    remark: str = ""
    skip_reason: str = ""
    # B's additions (§25.6)
    step_kind: str = ""
    optional: bool = False
    engine_action: bool = False
    action_state: str = ""            # "" | RUNNING | PASS | FAIL | ABORTED
    action_text: str = ""
    manual: bool = False
    origin: str = ""                  # B-BRU-08: auto | operator | auto+operator | cli-test


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
    closed_utc: str | None = None
    action_running: bool = False
    board_mismatch: str | None = None    # SWD-P3-04


@dataclass(frozen=True)
class BuPackage:
    path: str
    size_bytes: int
    files: int
    send_to: str


def default_steps() -> list[BuStep]:
    return [
        BuStep("6.3", "6", "Device line", "Connection & Config tab: read the Device line.",
               "FW 0.8.6, dict hash 0xF8BCDCB8 ✓", kind="auto", stop_if_fail=True, hg="HIL S-00",
               auto=BuAuto("PASS", "FW 0.8.6 (build 20261014-1115), dict hash 0xF8BCDCB8 ✓")),
        BuStep("6.9", "6", "Load cell sign", "Pull lightly on the load cell by hand and watch Readouts → Raw.",
               "Raw increases on pull", fields=(BuField("sign", "Raw on pull", "choice", choices=("increases", "decreases")),
                                                 BuField("ok", "I watched the Raw value", "confirm"))),
        BuStep("7.1", "7", "E-stop latch", "Press the red E-stop and keep it pressed for more than 1 s.",
               "ESTOP red, only ESTOP latched", kind="auto", stop_if_fail=True, hg="HG-32",
               auto=BuAuto("WAITING", "waiting for ESTOP …")),
        BuStep("8.4", "8", "D-42 ENA cut, MCU in reset", "Hold RESET, press the E-stop, turn the shaft by hand.",
               "the shaft turns freely", stop_if_fail=True, safety="Hands off the mouse while the observer touches "
               "the coupling.", fields=(BuField("free", "Shaft turns freely", "confirm"),
                                        BuField("m1", "M-1 across R_E", "number", "V", 0.5, 0.9, 2))),
        BuStep("9.4", "9", "HOME", "Press HOME (the normal confirmation appears).", "HOMED green `homed`",
               kind="action", action="home", motion=True, stop_if_fail=True,
               auto=BuAuto("WAITING", "waiting for HOMED …")),
        BuStep("9.2", "9", "Jog direction", "Manual tab: hold 'hold forward (+) ▶▶' for about 3 s.",
               "the table moves away from START", kind="action", action="show:manual", motion=True,
               fields=(BuField("dir_ok", "Moved away from START", "confirm"),)),
        BuStep("10.2.3", "10", "Pulse-integrity check", "Run the check: re-homes, round trips over the stroke at "
               "travel speed, final re-home.", "no HOME_DRIFT, final drift within the limit", kind="action",
               action="pulse_integrity", motion=True, engine_action=True, step_kind="pulse_integrity",
               hg="SYS-011"),
        BuStep("11.1", "11", "Save to NVM", "Save the configuration to the board.", "CFG green `clean`",
               kind="action", action="save_nvm", auto=BuAuto("WAITING", "CFG …")),
    ]


class FakeBringup:
    def __init__(self, owner: Any = None) -> None:
        self._o = owner
        self._steps: dict[str, BuStep] = {s.id: s for s in default_steps()}
        self._order = list(self._steps)
        self._sections = [BuSection("6", "6 PC software and first connection", ("6.3", "6.9")),
                          BuSection("7", "7 Input checks — 48 V off", ("7.1",)),
                          BuSection("8", "8 48 V on, ENABLE, D-42", ("8.4",)),
                          BuSection("9", "9 First motion", ("9.4", "9.2")),
                          BuSection("10", "10 Pulse integrity", ("10.2.3",)),
                          BuSection("11", "11 Save to NVM, restart", ("11.1",))]
        self._status = BuStatus()
        self.package: BuPackage | None = BuPackage(r"C:\Users\op\Documents\BirdBendStand\bringup\bringup_20261014_UID_20261014-1115.zip",
                                                   1_234_567, 23, "Send this file to the product owner (e-mail).")
        self.package_error: BaseException | None = None
        self.pending: Future | None = None
        self.run_refusal: GateResult | None = None
        self._closed: list[dict] = []

    def _rec(self, name: str, *args: Any, **kwargs: Any) -> None:
        if self._o is not None:
            self._o._rec(f"bringup.{name}", *args, **kwargs)

    # ------------------------------------------------------------------ queries
    def status(self) -> BuStatus:
        st = list(self._steps.values())
        return dataclasses.replace(self._status, total=len(st),
                                   done=sum(1 for s in st if s.state != "NOT_DONE"),
                                   passed=sum(1 for s in st if s.state == "PASS"),
                                   failed=sum(1 for s in st if s.state == "FAIL"),
                                   skipped=sum(1 for s in st if s.state == "SKIPPED"))

    def sections(self) -> list[BuSection]:
        return list(self._sections)

    def steps(self) -> list[BuStep]:
        return [self._steps[i] for i in self._order]

    def step(self, step_id: str) -> BuStep:
        return self._steps[step_id]

    # ------------------------------------------------------------------ test helpers
    def set_step(self, step_id: str, **changes: Any) -> BuStep:
        self._steps[step_id] = dataclasses.replace(self._steps[step_id], **changes)
        return self._steps[step_id]

    def connect_other_board(self, uid: str) -> None:
        """SWD-P3-04 test helper: another board is connected to the open session."""
        self.other_uid = uid
        self._status = dataclasses.replace(self._status, board_mismatch=(
            f"BOARD CHANGED: this session belongs to board {self._status.board_uid}, board {uid} is connected — "
            "start a new session for this board, or create the package of the old session"))

    def _mismatch(self) -> GateResult | None:
        if self._status.board_mismatch:
            return GateResult((GateItem("BOARD_CHANGED", Severity.REFUSE, self._status.board_mismatch),))
        return None

    # ------------------------------------------------------------------ commands
    def start(self, *, confirmed: bool = False) -> GateResult:
        self._rec("start", confirmed=confirmed)
        if self._status.active and not confirmed and self._status.board_mismatch:
            return GateResult((GateItem("SESSION_EXISTS", Severity.CONFIRM,
                                        f"board {self.other_uid} is connected, the session belongs to board "
                                        f"{self._status.board_uid} — start a new session for this board? The old "
                                        "session stays in its folder and can still be packed."),))
        if self._status.active and not confirmed:
            return GateResult((GateItem("SESSION_EXISTS", Severity.CONFIRM,
                                        f"a bring-up session exists ({self.status().done} steps done) — start a new "
                                        "one? The old session stays in its folder."),))
        uid = getattr(self, "other_uid", None) if self._status.board_mismatch else None
        self._status = BuStatus(True, "bu-1", uid or "0039ABCD", "20261014-1115", "2026-10-14T09:00:00Z",
                                current=self._order[0], folder=r"C:\Users\op\Documents\BirdBendStand\bringup\bu-1")
        return GATE_OK

    def select(self, step_id: str) -> None:
        self._rec("select", step_id)
        self._status = dataclasses.replace(self._status, current=step_id)

    def submit(self, step_id: str, entries: dict, remark: str = "") -> list[Issue]:
        self._rec("submit", step_id, dict(entries), remark=remark)
        if self._status.board_mismatch:
            return [Issue(None, IssueSeverity.ERROR, "BOARD_CHANGED", self._status.board_mismatch)]
        s = self._steps[step_id]
        issues: list[Issue] = []
        state = s.state
        for f in s.fields:
            v = entries.get(f.name)
            if f.required and (v is None or v == "" or (f.kind == "confirm" and v is not True)):
                issues.append(Issue(f.name, IssueSeverity.ERROR, "REQUIRED", f"{f.label}: entry required"))
            elif f.kind == "number" and v is not None and ((f.min is not None and v < f.min)
                                                            or (f.max is not None and v > f.max)):
                state = "FAIL"
        if not issues and state == "NOT_DONE":
            state = "PASS"
        if not [i for i in issues if i.severity == IssueSeverity.ERROR]:
            self._steps[step_id] = dataclasses.replace(s, entries=dict(entries), remark=remark, state=state)
        return issues

    def mark(self, step_id: str, state: str, *, reason: str = "", remark: str = "") -> GateResult:
        self._rec("mark", step_id, state, reason=reason, remark=remark)
        mm = self._mismatch()
        if mm is not None:
            return mm
        if state == "SKIPPED" and not reason.strip():
            return GateResult((GateItem("REASON_REQUIRED", Severity.REFUSE, "a reason is required to skip a step"),))
        self._steps[step_id] = dataclasses.replace(self._steps[step_id], state=state, skip_reason=reason,
                                                   remark=remark or self._steps[step_id].remark)
        return GATE_OK

    # ------------------------------------------------------------------ B's additions (§25.6)
    def run_action(self, step_id: str, *, confirmed: bool = False) -> GateResult:
        self._rec("run_action", step_id, confirmed=confirmed)
        mm = self._mismatch()
        if mm is not None:
            return mm
        s = self._steps[step_id]
        if not s.engine_action:
            return GateResult((GateItem("NOT_AN_ENGINE_ACTION", Severity.REFUSE, "not an engine action"),))
        if self.run_refusal is not None:
            return self.run_refusal
        if not confirmed:
            return GateResult((GateItem("BRINGUP_MOTION", Severity.CONFIRM,
                                        "the axis moves: 3 re-homes, 10 round trips 5 ↔ 285 mm at travel speed, a "
                                        "final re-home — nothing in the travel, no specimen"),))
        self.set_step(step_id, action_state="RUNNING", action_text="baseline re-home 1/3")
        self._status = dataclasses.replace(self._status, action_running=True)
        return GATE_OK

    def cancel_action(self) -> None:
        self._rec("cancel_action")
        for sid, s in self._steps.items():
            if s.action_state == "RUNNING":
                self.set_step(sid, action_state="ABORTED", action_text="cancelled — run the check again from the "
                                                                        "baseline")
        self._status = dataclasses.replace(self._status, action_running=False)

    def close_session(self, why: str = "operator") -> GateResult:
        self._rec("close_session", why)
        if not self._status.active:
            return GateResult((GateItem("NO_SESSION", Severity.REFUSE, "no bring-up session"),))
        self._closed.append({"session_id": self._status.session_id, "closed": True, "why": why})
        self._status = dataclasses.replace(self._status, active=False, closed_utc="2026-10-14T12:00:00Z")
        return GATE_OK

    def resume(self, session_id: str) -> GateResult:
        self._rec("resume", session_id)
        if not any(c["session_id"] == session_id for c in self._closed):
            return GateResult((GateItem("UNKNOWN_SESSION", Severity.REFUSE, f"unknown session {session_id}"),))
        self._status = dataclasses.replace(self._status, active=True, closed_utc=None)
        return GATE_OK

    def sessions(self) -> list[dict]:
        cur = [{"session_id": self._status.session_id, "closed": False}] if self._status.active else []
        return cur + list(self._closed)

    def create_package_async(self) -> Future:
        self._rec("create_package_async")
        f: Future = Future()
        if self.pending is not None:
            return self.pending
        if self.package_error is not None:
            f.set_exception(self.package_error)
        else:
            f.set_result(self.package)
        return f
